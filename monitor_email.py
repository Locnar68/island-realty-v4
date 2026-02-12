#!/usr/bin/env python3
"""
Island Realty PMS - Gmail Monitor
Checks for new MLS property emails and processes them
"""
import os
import sys
import json
import pickle
import base64
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import psycopg2
from anthropic import Anthropic

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv('/opt/island-realty/.env')

# Configuration
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.modify']
CREDS_FILE = '/opt/island-realty/config/gmail-credentials.json'
TOKEN_FILE = '/opt/island-realty/config/token.pickle'
DB_PASSWORD = os.getenv('DB_PASSWORD', 'Pepmi@12')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

if not ANTHROPIC_API_KEY:
    print("✗ ANTHROPIC_API_KEY not set!")
    sys.exit(1)

def get_gmail_service():
    """Authenticate and return Gmail service"""
    with open(TOKEN_FILE, 'rb') as token:
        creds = pickle.load(token)
    return build('gmail', 'v1', credentials=creds)

def get_db_connection():
    """Connect to PostgreSQL"""
    return psycopg2.connect(
        dbname='island_properties',
        user='island_user',
        password=DB_PASSWORD,
        host='localhost'
    )

def extract_property_data(email_text):
    """Use Claude AI to extract property data from email"""
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    
    prompt = f"""Extract property information from this email. Return ONLY valid JSON:

{email_text[:2000]}

Extract:
- mls_number (string)
- address (string) 
- price (number, no dollar signs or commas)
- status (string: "Active", "Pending", "Sold", "Back on Market")

Return JSON format: {{"mls_number": "...", "address": "...", "price": 0, "status": "..."}}"""
    
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    response_text = message.content[0].text.strip()
    # Remove markdown code blocks if present
    if response_text.startswith('```'):
        response_text = response_text.split('\n', 1)[1]
        response_text = response_text.rsplit('\n```', 1)[0]
    
    return json.loads(response_text)

def decode_body(body_data):
    """Decode base64 email body"""
    if body_data:
        return base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
    return ""

def process_emails():
    """Main email processing loop"""
    print(f"[{datetime.now()}] Starting email check...")
    
    # Connect to Gmail
    service = get_gmail_service()
    
    # Get unread messages about properties
    results = service.users().messages().list(
        userId='me',
        q='is:unread (subject:MLS OR subject:property OR subject:listing OR subject:BOM OR subject:price)',
        maxResults=10
    ).execute()
    
    messages = results.get('messages', [])
    print(f"Found {len(messages)} unread property emails")
    
    if not messages:
        return
    
    # Connect to database
    conn = get_db_connection()
    cur = conn.cursor()
    
    for msg in messages:
        try:
            # Get message details
            message = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='full'
            ).execute()
            
            # Extract email body
            payload = message['payload']
            body = ""
            
            if 'parts' in payload:
                for part in payload['parts']:
                    if part['mimeType'] == 'text/plain':
                        body += decode_body(part['body'].get('data', ''))
            elif 'body' in payload:
                body = decode_body(payload['body'].get('data', ''))
            
            if not body:
                print(f"  Skipping - no text body")
                continue
            
            # Extract property data using Claude
            print(f"  Processing email...")
            property_data = extract_property_data(body)
            
            # Validate we got data
            if not property_data.get('mls_number'):
                print(f"  ✗ No MLS number found")
                continue
            
            # Save to database
            cur.execute("""
                INSERT INTO properties (mls_number, address, price, status)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (mls_number) DO UPDATE
                SET address = EXCLUDED.address,
                    price = EXCLUDED.price,
                    status = EXCLUDED.status,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                property_data['mls_number'],
                property_data['address'],
                property_data.get('price', 0),
                property_data['status']
            ))
            
            conn.commit()
            print(f"✓ Processed: {property_data['mls_number']} - {property_data['address'][:50]}")
            
            # Mark as read
            service.users().messages().modify(
                userId='me',
                id=msg['id'],
                body={'removeLabelIds': ['UNREAD']}
            ).execute()
            
        except Exception as e:
            print(f"✗ Error processing message: {e}")
            continue
    
    cur.close()
    conn.close()
    print(f"[{datetime.now()}] Email check complete")

if __name__ == '__main__':
    process_emails()
