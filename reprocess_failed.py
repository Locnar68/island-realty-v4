#!/usr/bin/env python3
"""Reprocess emails that failed due to missing API key"""

import os, sys, time, json, base64, pickle
sys.path.insert(0, '/opt/island-realty')

from app.email_processor import EmailProcessor
from app.models import db_connection, EmailProcessingLog
from psycopg2.extras import RealDictCursor
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

def extract_body(payload):
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
        data = payload.get('body', {}).get('data', '')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    return body

def get_attachments(payload, message_id, depth=0):
    attachments = []
    if 'parts' in payload:
        for part in payload['parts']:
            filename = part.get('filename', '')
            if filename and not filename.startswith('image00'):
                att_id = part.get('body', {}).get('attachmentId', '')
                attachments.append({
                    'filename': filename,
                    'size': part.get('body', {}).get('size', 0),
                    'mimeType': part.get('mimeType', ''),
                    'gmail_attachment_id': att_id,
                    'gmail_message_id': message_id
                })
            if 'parts' in part:
                attachments.extend(get_attachments(part, message_id, depth+1))
    return attachments

# Load Gmail service
with open('/opt/island-realty/config/token.pickle', 'rb') as f:
    creds = pickle.load(f)
gmail = build('gmail', 'v1', credentials=creds)

# Init AI processor
api_key = os.getenv('ANTHROPIC_API_KEY')
if not api_key:
    # Load from .env manually
    with open('/opt/island-realty/.env') as f:
        for line in f:
            if line.startswith('ANTHROPIC_API_KEY='):
                api_key = line.strip().split('=', 1)[1]
                break

if not api_key:
    print("ERROR: No API key found!")
    sys.exit(1)

processor = EmailProcessor(api_key)

# Get failed emails
with db_connection() as conn:
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT email_id, email_subject, email_from, email_date, property_id
        FROM email_processing_log
        WHERE actions_taken::text LIKE '%no_property_data%'
        AND processing_time_ms < 100
        AND email_subject LIKE '%:%'
        ORDER BY email_date DESC
    """)
    failed = cursor.fetchall()

print(f"Found {len(failed)} emails to reprocess\n")

success_count = 0
for row in failed:
    email_id = row['email_id']
    print(f"--- {row['email_subject']} ---")
    
    try:
        # Fetch from Gmail
        msg = gmail.users().messages().get(userId='me', id=email_id, format='full').execute()
        body = extract_body(msg['payload'])
        subject = row['email_subject']
        from_email = row['email_from']
        date_str = str(row['email_date'])
        
        # Run AI extraction
        start = time.time()
        result = processor.process_email({
            "id": email_id,
            "subject": subject,
            "body": body,
            "from": from_email,
            "date": date_str
        })
        elapsed = int((time.time() - start) * 1000)
        
        success = result.get('_metadata', {}).get('success', False)
        prop_data = result.get('property_data', {})
        
        if not success:
            print(f"  AI ERROR: {result.get('_metadata', {}).get('error', 'unknown')}")
            continue
        
        if not prop_data:
            print(f"  AI returned no property data")
            continue
            
        address = prop_data.get('address', '')
        mls = prop_data.get('mls_number', '')
        status = result.get('status_change', {}).get('new_status', '')
        price = prop_data.get('current_list_price')
        
        print(f"  AI extracted: addr={address}, mls={mls}, status={status}, price={price} ({elapsed}ms)")
        
        # Find matching property
        with db_connection() as conn2:
            cur2 = conn2.cursor(cursor_factory=RealDictCursor)
            
            prop_id = row['property_id']  # May already be linked from earlier fix
            
            if not prop_id and mls:
                cur2.execute("SELECT id FROM properties WHERE mls_number = %s", (mls,))
                r = cur2.fetchone()
                if r: prop_id = r['id']
            
            if not prop_id and address:
                cur2.execute("SELECT id FROM properties WHERE LOWER(REPLACE(address, ',', '')) LIKE LOWER(%s) LIMIT 1", 
                           (f'%{address}%',))
                r = cur2.fetchone()
                if r: prop_id = r['id']
            
            if prop_id:
                # Update the log entry
                cur2.execute("""
                    UPDATE email_processing_log 
                    SET property_id = %s, 
                        processing_time_ms = %s,
                        actions_taken = %s::jsonb
                    WHERE email_id = %s
                """, (prop_id, elapsed, json.dumps(['reprocessed', f'found_property_{prop_id}']), email_id))
                
                # Update property status if needed
                if status:
                    cur2.execute("SELECT current_status FROM properties WHERE id = %s", (prop_id,))
                    current = cur2.fetchone()
                    if current and current['current_status'] != status:
                        cur2.execute("""
                            UPDATE properties SET current_status = %s, updated_at = NOW() WHERE id = %s
                        """, (status, prop_id))
                        print(f"  Updated prop {prop_id} status: {current['current_status']} → {status}")
                
                # Update price if it's a price change
                if price and price > 0:
                    cur2.execute("""
                        UPDATE properties SET current_list_price = %s, updated_at = NOW() WHERE id = %s
                    """, (price, prop_id))
                    print(f"  Updated prop {prop_id} price: ${price:,.0f}")
                
                conn2.commit()
                print(f"  ✅ Linked to property {prop_id}")
                success_count += 1
            else:
                print(f"  ⚠️ No matching property found for: {address}")
        
        time.sleep(0.5)  # Rate limit
        
    except Exception as e:
        print(f"  ❌ Error: {e}")

print(f"\nReprocessed: {success_count}/{len(failed)}")
