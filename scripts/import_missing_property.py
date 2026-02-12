#!/usr/bin/env python3
"""
Find and Import Missing Property Email
Searches Gmail for specific email and imports it
"""

import sys
import os
import pickle
from datetime import datetime
from googleapiclient.discovery import build
import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, '/opt/island-realty/app')
from email_processor import EmailProcessor
from models import EmailProcessingLog
import base64
from bs4 import BeautifulSoup

def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="island_properties",
        user="island_user",
        password="island123!"
    )

def authenticate():
    """Authenticate with Gmail API"""
    token_path = '/opt/island-realty/config/token.pickle'
    with open(token_path, 'rb') as token:
        creds = pickle.load(token)
    
    return build('gmail', 'v1', credentials=creds)

def extract_body(payload):
    """Extract email body from payload"""
    body = ""
    
    if 'parts' in payload:
        for part in payload['parts']:
            mime = part.get('mimeType', '')
            if mime == 'text/plain':
                data = part['body'].get('data', '')
                if data:
                    body += base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            elif mime == 'text/html':
                data = part['body'].get('data', '')
                if data:
                    html = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(html, 'html.parser')
                    body += soup.get_text()
            elif mime.startswith('multipart/'):
                body += extract_body(part)
    else:
        data = payload['body'].get('data', '')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    
    return body

def get_attachment_info(payload, message_id, depth=0):
    """Get attachment information"""
    attachments = []
    
    if 'parts' in payload:
        for part in payload['parts']:
            filename = part.get('filename', '')
            if filename:
                attachment_id = part['body'].get('attachmentId')
                is_foil = 'foil' in filename.lower()
                
                attachment = {
                    'filename': filename,
                    'mimeType': part.get('mimeType', 'application/octet-stream'),
                    'size': part['body'].get('size', 0),
                    'attachmentId': attachment_id,
                    'gmail_message_id': message_id,
                    'is_foil': is_foil
                }
                attachments.append(attachment)
            
            if 'parts' in part and depth < 5:
                attachments.extend(get_attachment_info(part, message_id, depth + 1))
    
    return attachments

def search_and_import_email(service, search_query, address_to_find):
    """Search for email and import it"""
    print(f"\nSearching for: {search_query}")
    
    results = service.users().messages().list(
        userId='me',
        q=search_query,
        maxResults=50
    ).execute()
    
    messages = results.get('messages', [])
    print(f"Found {len(messages)} matching emails")
    
    # Search through messages for the specific address
    for msg in messages:
        message = service.users().messages().get(
            userId='me',
            id=msg['id'],
            format='full'
        ).execute()
        
        headers = message['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
        from_email = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown')
        date_str = next((h['value'] for h in headers if h['name'].lower() == 'date'), None)
        
        body = extract_body(message['payload'])
        
        # Check if this email contains the address we're looking for
        if address_to_find.lower() in body.lower() or address_to_find.lower() in subject.lower():
            print(f"\n✓ Found email!")
            print(f"  Subject: {subject}")
            print(f"  From: {from_email}")
            print(f"  Date: {date_str}")
            
            # Check if already processed
            if EmailProcessingLog.is_processed(msg['id']):
                print(f"  ⚠ Already processed - skipping")
                continue
            
            # Get attachments
            attachments = get_attachment_info(message['payload'], msg['id'])
            
            # Process the email
            processor = EmailProcessor(os.getenv("ANTHROPIC_API_KEY"))
            
            email_data = {
                'id': msg['id'],
                'subject': subject,
                'from': from_email,
                'date': date_str,
                'body': body,
                'attachments': attachments
            }
            
            print(f"\n  Processing with AI...")
            extracted_data = processor.process_email(email_data)
            
            if extracted_data and extracted_data.get('property_data'):
                print(f"  ✓ Extracted property data:")
                prop_data = extracted_data['property_data']
                print(f"    Address: {prop_data.get('address')}")
                print(f"    MLS: {prop_data.get('mls_number')}")
                print(f"    Price: {prop_data.get('current_list_price')}")
                
                status_change = extracted_data.get('status_change', {})
                if status_change.get('new_status'):
                    print(f"    Status: {status_change['new_status']}")
                
                # Save to database
                conn = get_db_connection()
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                
                try:
                    # Create property
                    cursor.execute("""
                        INSERT INTO properties 
                        (address, city, zip_code, current_list_price, current_status, 
                         data_source, last_email_id, email_subject, email_from, email_date)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING id
                    """, (
                        prop_data.get('address'),
                        prop_data.get('city'),
                        prop_data.get('zip_code'),
                        prop_data.get('current_list_price'),
                        status_change.get('new_status', 'Active'),
                        'email',
                        msg['id'],
                        subject,
                        from_email,
                        date_str
                    ))
                    
                    row = cursor.fetchone()
                    if row:
                        property_id = row['id']
                        print(f"\n  ✓ Created property ID {property_id}")
                        
                        # Link email
                        cursor.execute("""
                            INSERT INTO property_emails 
                            (property_id, gmail_message_id, email_subject, email_body, 
                             email_from, email_date, has_attachments, attachment_count)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (gmail_message_id) DO NOTHING
                        """, (
                            property_id,
                            msg['id'],
                            subject,
                            body[:5000],  # Truncate long bodies
                            from_email,
                            date_str,
                            len(attachments) > 0,
                            len(attachments)
                        ))
                        
                        # Save attachments
                        for att in attachments:
                            is_foil = att.get('is_foil', False)
                            category = 'FOIL' if is_foil else 'General'
                            
                            cursor.execute("""
                                INSERT INTO attachments 
                                (property_id, filename, file_size, mime_type, category,
                                 source_email_id, gmail_attachment_id, gmail_message_id, 
                                 is_foil, uploaded_by)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT DO NOTHING
                            """, (
                                property_id,
                                att['filename'],
                                att.get('size', 0),
                                att.get('mimeType'),
                                category,
                                msg['id'],
                                att.get('attachmentId'),
                                att.get('gmail_message_id'),
                                is_foil,
                                'manual_import'
                            ))
                        
                        # Log processing
                        EmailProcessingLog.log(
                            email_id=msg['id'],
                            email_subject=subject,
                            email_from=from_email,
                            email_date=date_str,
                            status='success',
                            property_id=property_id,
                            actions_taken='["manual_import", "property_created"]',
                            ai_model_used='claude-sonnet-4'
                        )
                        
                        conn.commit()
                        print(f"  ✓ Successfully imported property!")
                        return True
                    else:
                        print(f"  ⚠ Property may already exist")
                        conn.rollback()
                        
                except Exception as e:
                    conn.rollback()
                    print(f"  ✗ Database error: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    cursor.close()
                    conn.close()
            else:
                print(f"  ⚠ Could not extract property data from email")
    
    return False

def main():
    print("=" * 60)
    print("MISSING PROPERTY EMAIL IMPORT")
    print("=" * 60)
    
    # Authenticate
    print("\nAuthenticating with Gmail...")
    service = authenticate()
    print("✓ Authenticated")
    
    # Search for the specific email
    # Looking for: 140 Arlington Avenue, Valley Stream, NY - email dated 2/5/2026
    
    search_queries = [
        'subject:"new list price" after:2026/02/04 before:2026/02/06',
        '140 Arlington Avenue Valley Stream after:2026/02/04 before:2026/02/06',
        'Arlington Valley Stream after:2026/02/04 before:2026/02/06'
    ]
    
    address = "140 Arlington Avenue"
    
    for query in search_queries:
        if search_and_import_email(service, query, address):
            print("\n✓ SUCCESS: Property imported")
            return
    
    print("\n✗ Could not find the email. Try broader search or different date range.")

if __name__ == '__main__':
    main()

