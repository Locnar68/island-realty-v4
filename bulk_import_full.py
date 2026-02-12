#!/usr/bin/env python3
"""
FULL bulk import with pagination - gets ALL emails
"""
import os
import sys
import json
import pickle
import base64
import time
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
    try:
        soup = BeautifulSoup(html, 'html.parser')
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text()
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
    count = 0
    if 'parts' in payload:
        for part in payload['parts']:
            if part.get('filename'):
                count += 1
    return count

def extract_body_from_payload(payload):
    body = ""
    
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                body += decode_body(part['body'].get('data', ''))
        
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
    print(f"[{datetime.now()}] Starting FULL bulk import with pagination...")
    
    service = get_gmail_service()
    
    # Get count first
    query = 'subject:MLS OR subject:property OR subject:listing OR subject:BOM OR subject:price OR from:@iarny.com'
    results = service.users().messages().list(userId='me', q=query).execute()
    total_estimate = results.get('resultSizeEstimate', 0)
    
    print(f"Estimated {total_estimate} property emails in Gmail")
    print(f"Starting paginated retrieval...\n")
    
    # Get ALL messages with pagination
    all_messages = []
    page_token = None
    page = 0
    
    while True:
        page += 1
        print(f"Fetching page {page}...", end=" ")
        
        results = service.users().messages().list(
            userId='me',
            q=query,
            maxResults=500,
            pageToken=page_token
        ).execute()
        
        messages = results.get('messages', [])
        all_messages.extend(messages)
        print(f"Got {len(messages)} emails (total: {len(all_messages)})")
        
        page_token = results.get('nextPageToken')
        if not page_token:
            break
        
        time.sleep(0.5)  # Rate limit protection
    
    print(f"\nTotal emails retrieved: {len(all_messages)}")
    print(f"Starting property extraction...\n")
    
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
    
    for i, msg in enumerate(all_messages, 1):
        try:
            if i % 50 == 0:
                print(f"[{i}/{len(all_messages)}] Progress: {imported} imported, {skipped} skipped, {errors} errors")
            
            message = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='full'
            ).execute()
            
            headers = message['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '')
            
            payload = message['payload']
            body = extract_body_from_payload(payload)
            
            if not body or len(body.strip()) < 20:
                skipped += 1
                continue
            
            attachment_count = count_attachments(payload)
            has_attachments = attachment_count > 0
            
            property_data = extract_property_data(body, subject)
            
            if not property_data.get('mls_number'):
                skipped += 1
                continue
            
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
            imported += 1
            
            if i % 10 == 0:
                time.sleep(1)  # Rate limit protection
            
        except Exception as e:
            errors += 1
            continue
    
    cur.close()
    conn.close()
    
    print(f"\n{'='*60}")
    print(f"FULL import complete!")
    print(f"  Total emails: {len(all_messages)}")
    print(f"  Imported: {imported}")
    print(f"  Skipped: {skipped}")
    print(f"  Errors: {errors}")
    print(f"{'='*60}")

if __name__ == '__main__':
    bulk_import()
