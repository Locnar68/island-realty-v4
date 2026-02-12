#!/usr/bin/env python3
"""
Retroactive Email Matching Script
Searches Gmail for emails about properties and links them to existing database records
"""

import os
import sys
import pickle
import base64
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import re

# Add app directory to path
sys.path.insert(0, '/opt/island-realty/app')

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import psycopg2
from psycopg2.extras import RealDictCursor

print("=" * 80)
print("RETROACTIVE EMAIL MATCHING SCRIPT")
print("=" * 80)
print()

# Gmail authentication
token_path = '/opt/island-realty/config/token.pickle'
with open(token_path, 'rb') as token:
    creds = pickle.load(token)

service = build('gmail', 'v1', credentials=creds)

# Database connection
conn = psycopg2.connect(
    dbname="island_properties",
    user="island_user",
    password="Pepmi@12",
    host="localhost"
)
cursor = conn.cursor(cursor_factory=RealDictCursor)

# Get all properties with their addresses
cursor.execute("""
    SELECT id, address, current_status, reo_status_date 
    FROM properties 
    ORDER BY id
""")
properties = cursor.fetchall()

print(f"Found {len(properties)} properties in database")
print()

def extract_body(payload):
    """Extract email body from payload"""
    body = ""
    
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                data = part['body'].get('data', '')
                if data:
                    body += base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            elif part['mimeType'] == 'text/html':
                data = part['body'].get('data', '')
                if data:
                    html = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(html, 'html.parser')
                    body += soup.get_text()
    else:
        data = payload['body'].get('data', '')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    
    return body

def get_attachment_info(payload):
    """Get attachment information"""
    attachments = []
    
    if 'parts' in payload:
        for part in payload['parts']:
            if part.get('filename'):
                attachments.append({
                    'filename': part['filename'],
                    'mimeType': part['mimeType'],
                    'size': part['body'].get('size', 0)
                })
    
    return attachments

def normalize_address(address):
    """Normalize address for matching"""
    if not address:
        return ""
    
    # Remove common variations
    normalized = address.upper().strip()
    normalized = normalized.replace('STREET', 'ST')
    normalized = normalized.replace('AVENUE', 'AVE')  
    normalized = normalized.replace('ROAD', 'RD')
    normalized = normalized.replace('BOULEVARD', 'BLVD')
    normalized = normalized.replace('UNIT', '')
    normalized = normalized.replace('#', '')
    normalized = normalized.replace(',', '')
    normalized = normalized.replace('.', '')
    
    # Remove extra spaces
    normalized = ' '.join(normalized.split())
    
    return normalized

def parse_date(date_str):
    """Parse email date to timestamp"""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        return dt
    except:
        return None

# Search Gmail for property-related emails (last 6 months)
since_date = datetime.now() - timedelta(days=180)
query = f'after:{int(since_date.timestamp())}'

print(f"Searching Gmail for emails since {since_date.strftime('%Y-%m-%d')}...")

try:
    results = service.users().messages().list(
        userId='me',
        q=query,
        maxResults=500
    ).execute()
    
    messages = results.get('messages', [])
    print(f"Found {len(messages)} emails to process")
    print()
    
    matched_count = 0
    processed_count = 0
    
    for msg_ref in messages:
        processed_count += 1
        msg_id = msg_ref['id']
        
        # Get full message
        message = service.users().messages().get(
            userId='me',
            id=msg_id,
            format='full'
        ).execute()
        
        # Extract headers
        headers = message['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
        from_email = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
        date_str = next((h['value'] for h in headers if h['name'].lower() == 'date'), '')
        
        # Extract body and attachments
        body = extract_body(message['payload'])
        attachments = get_attachment_info(message['payload'])
        
        # Parse date
        email_date = parse_date(date_str)
        
        # Try to match to a property
        matched = False
        for prop in properties:
            prop_address_normalized = normalize_address(prop['address'])
            
            # Check if property address appears in subject or body
            if (prop_address_normalized in normalize_address(subject) or 
                prop_address_normalized in normalize_address(body)):
                
                # Check if email already linked to this property
                cursor.execute("""
                    SELECT id FROM property_emails 
                    WHERE property_id = %s AND gmail_message_id = %s
                """, (prop['id'], msg_id))
                
                if cursor.fetchone():
                    continue  # Already linked
                
                # Create property_email record
                try:
                    cursor.execute("""
                        INSERT INTO property_emails 
                        (property_id, gmail_message_id, email_subject, email_from, email_date, 
                         email_body, has_attachments, attachment_count, attachment_names)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        prop['id'],
                        msg_id,
                        subject[:500] if subject else '',
                        from_email[:200] if from_email else '',
                        email_date,
                        body[:1000] if body else '',
                        len(attachments) > 0,
                        len(attachments),
                        [a['filename'] for a in attachments] if attachments else []
                    ))
                    
                    conn.commit()
                    matched_count += 1
                    print(f"✓ Matched: {prop['address'][:40]} <- {subject[:40]}")
                    matched = True
                    break
                    
                except Exception as e:
                    conn.rollback()
                    print(f"  Error linking email: {e}")
        
        if processed_count % 50 == 0:
            print(f"  Processed {processed_count}/{len(messages)} emails, {matched_count} matched...")
    
    print()
    print("=" * 80)
    print(f"COMPLETED: Matched {matched_count} emails to {len(properties)} properties")
    print("=" * 80)
    
    # Show summary
    cursor.execute("""
        SELECT 
            p.current_status,
            COUNT(DISTINCT pe.id) as email_count,
            COUNT(DISTINCT p.id) as property_count
        FROM properties p
        LEFT JOIN property_emails pe ON p.id = pe.property_id
        GROUP BY p.current_status
        ORDER BY p.current_status
    """)
    
    print("\nEmail Tracking Summary:")
    print("-" * 80)
    for row in cursor.fetchall():
        print(f"  {row['current_status']:20s}: {row['email_count']:3d} emails across {row['property_count']:3d} properties")
    
except HttpError as error:
    print(f"Gmail API Error: {error}")
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    cursor.close()
    conn.close()
