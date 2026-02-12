#!/usr/bin/env python3
"""
Bulk import ALL property emails from Gmail history
"""
import os
import sys
import json
import pickle
import base64
from datetime import datetime
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from googleapiclient.discovery import build
import psycopg2
from anthropic import Anthropic

load_dotenv('/opt/island-realty/.env')

TOKEN_FILE = '/opt/island-realty/config/token.pickle'
DB_PASSWORD = os.getenv('DB_PASSWORD', 'Pepmi@12')
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')

def get_gmail_service():
    with open(TOKEN_FILE, 'rb') as token:
        creds = pickle.load(token)
    return build('gmail', 'v1', credentials=creds)

def decode_body(body_data):
    if body_data:
        return base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
    return ""

def extract_text_from_html(html):
    """Extract text from HTML email"""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text()
        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        return text
    except:
        return ""

def extract_property_data(email_text, subject):
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    
    prompt = f"""Extract property information from this email. Return ONLY valid JSON:

Subject: {subject}
Body:
{email_text[:2000]}

Extract:
- mls_number (string)
- address (string) 
- price (number, no dollar signs or commas)
- status (string: "Active", "Pending", "Sold", "Back on Market", "Price Reduction")

Return JSON format: {{"mls_number": "...", "address": "...", "price": 0, "status": "..."}}"""
    
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    response_text = message.content[0].text.strip()
    if response_text.startswith('```'):
        response_text = response_text.split('\n', 1)[1]
        response_text = response_text.rsplit('\n```', 1)[0]
    
    return json.loads(response_text)

def count_attachments(payload):
    """Count attachments in email"""
    count = 0
    if 'parts' in payload:
        for part in payload['parts']:
            if part.get('filename'):
                count += 1
    return count

def extract_body_from_payload(payload):
    """Extract body from email payload - handles both plain text and HTML"""
    body = ""
    
    # Try plain text first
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                body += decode_body(part['body'].get('data', ''))
        
        # If no plain text, try HTML
        if not body:
            for part in payload['parts']:
                if part['mimeType'] == 'text/html':
                    html = decode_body(part['body'].get('data', ''))
                    body += extract_text_from_html(html)
    elif 'body' in payload:
        if payload.get('mimeType') == 'text/html':
            html = decode_body(payload['body'].get('data', ''))
            body = extract_text_from_html(html)
        else:
            body = decode_body(payload['body'].get('data', ''))
    
    return body

def bulk_import():
    print(f"[{datetime.now()}] Starting bulk import...")
    
    service = get_gmail_service()
    
    # Get ALL property-related emails (not just unread)
    results = service.users().messages().list(
        userId='me',
        q='subject:MLS OR subject:property OR subject:listing OR subject:BOM OR subject:price OR from:@iarny.com',
        maxResults=500  # Import up to 500 emails
    ).execute()
    
    messages = results.get('messages', [])
    print(f"Found {len(messages)} total property emails in history")
    
    if not messages:
        print("No messages found")
        return
    
    conn = psycopg2.connect(
        dbname='island_properties',
        user='island_user',
        password=DB_PASSWORD,
        host='localhost'
    )
    cur = conn.cursor()
    
    imported = 0
    skipped = 0
    errors = 0
    
    for i, msg in enumerate(messages, 1):
        try:
            print(f"\r[{i}/{len(messages)}] Processing...", end=" ", flush=True)
            
            message = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='full'
            ).execute()
            
            # Get subject
            headers = message['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
            
            # Extract body (plain text OR HTML)
            payload = message['payload']
            body = extract_body_from_payload(payload)
            
            if not body or len(body.strip()) < 20:
                print("No content")
                skipped += 1
                continue
            
            # Count attachments
            attachment_count = count_attachments(payload)
            has_attachments = attachment_count > 0
            
            # Extract property data
            property_data = extract_property_data(body, subject)
            
            if not property_data.get('mls_number'):
                print("No MLS")
                skipped += 1
                continue
            
            # Save to database with gmail_message_id
            cur.execute("""
                INSERT INTO properties (mls_number, address, price, status, has_attachments, attachment_count, gmail_message_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (mls_number) DO UPDATE
                SET address = EXCLUDED.address,
                    price = EXCLUDED.price,
                    status = EXCLUDED.status,
                    has_attachments = EXCLUDED.has_attachments,
                    attachment_count = EXCLUDED.attachment_count,
                    gmail_message_id = EXCLUDED.gmail_message_id,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                property_data['mls_number'],
                property_data['address'],
                property_data.get('price', 0),
                property_data['status'],
                has_attachments,
                attachment_count,
                msg['id']
            ))
            
            conn.commit()
            print(f"✓ {property_data['mls_number']} ({attachment_count} attach)")
            imported += 1
            
        except Exception as e:
            print(f"✗ Error: {str(e)[:50]}")
            errors += 1
            continue
    
    cur.close()
    conn.close()
    
    print(f"\n{'='*60}")
    print(f"Bulk import complete!")
    print(f"  Imported: {imported}")
    print(f"  Skipped: {skipped}")
    print(f"  Errors: {errors}")
    print(f"{'='*60}")

if __name__ == '__main__':
    bulk_import()
