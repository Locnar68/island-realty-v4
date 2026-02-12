#!/usr/bin/env python3
"""
Reprocess Emails from INBOX - Populate Email Content
Fetches property emails from INBOX and updates database
"""

import os
import sys
import pickle
import psycopg2
from googleapiclient.discovery import build
from anthropic import Anthropic
import json
import re
from datetime import datetime
import base64
from email.utils import parsedate_to_datetime

# Configuration
GMAIL_TOKEN_PATH = '/opt/island-realty/config/token.pickle'
DB_CONFIG = {
    'dbname': 'island_properties',
    'user': 'island_user',
    'password': 'Pepmi@12',
    'host': 'localhost'
}

class EmailReprocessor:
    def __init__(self):
        self.gmail_service = None
        self.anthropic_client = None
        self.db_conn = None
        self.processed_count = 0
        self.updated_count = 0
        self.skipped_count = 0
        
    def authenticate_gmail(self):
        """Authenticate with Gmail API"""
        print("🔐 Authenticating with Gmail...")
        with open(GMAIL_TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)
        self.gmail_service = build('gmail', 'v1', credentials=creds)
        print("✅ Gmail authenticated")
        
    def connect_anthropic(self):
        """Connect to Anthropic API"""
        print("🤖 Connecting to Claude AI...")
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            raise Exception("ANTHROPIC_API_KEY not set")
        self.anthropic_client = Anthropic(api_key=api_key)
        print("✅ Claude AI connected")
        
    def connect_db(self):
        """Connect to PostgreSQL"""
        print("🗄️  Connecting to database...")
        self.db_conn = psycopg2.connect(**DB_CONFIG)
        print("✅ Database connected")
        
    def get_property_emails(self):
        """Get property-related emails from INBOX"""
        print("\n📧 Fetching property emails from INBOX...")
        
        # Search for emails with property-related subjects
        query = 'subject:(property OR price OR MLS OR listing OR "new list" OR reduction OR "highest & best")'
        
        results = self.gmail_service.users().messages().list(
            userId='me',
            q=query,
            maxResults=100
        ).execute()
        
        messages = results.get('messages', [])
        print(f"✅ Found {len(messages)} property emails")
        return messages
        
    def extract_email_body(self, payload):
        """Extract email body from Gmail payload"""
        def get_body_recursive(part):
            if 'parts' in part:
                for subpart in part['parts']:
                    result = get_body_recursive(subpart)
                    if result:
                        return result
            else:
                if part['mimeType'] == 'text/html':
                    data = part['body'].get('data', '')
                    if data:
                        return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                elif part['mimeType'] == 'text/plain':
                    data = part['body'].get('data', '')
                    if data:
                        return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            return None
        
        body = get_body_recursive(payload)
        return body if body else ''
        
    def process_email_with_claude(self, email_subject, email_body):
        """Use Claude to extract property information"""
        prompt = f"""Analyze this real estate email and extract property information.

Email Subject: {email_subject}
Email Body:
{email_body[:2000]}

Extract and return ONLY valid JSON with these fields:
{{
    "address": "full property address with city, state, zip",
    "mls_number": "MLS number if present, otherwise null",
    "price": numeric value or null,
    "status": "Active" or "Price Reduction" or "Highest & Best" or "Pending" or "Sold"
}}"""

        try:
            message = self.anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = message.content[0].text
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            
            if json_match:
                return json.loads(json_match.group())
            return None
            
        except Exception as e:
            print(f"  ⚠️  Claude error: {e}")
            return None
            
    def check_property_exists(self, mls_number):
        """Check if property already exists by MLS number"""
        cursor = self.db_conn.cursor()
        cursor.execute(
            "SELECT id FROM properties WHERE mls_number = %s",
            (mls_number,)
        )
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result else None
        
    def update_property_email(self, property_id, email_data):
        """Update existing property with email content"""
        cursor = self.db_conn.cursor()
        cursor.execute("""
            UPDATE properties 
            SET email_subject = %s,
                email_body = %s,
                email_from = %s,
                email_date = %s,
                gmail_message_id = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (
            email_data['subject'],
            email_data['body'],
            email_data['from'],
            email_data['date'],
            email_data['gmail_message_id'],
            property_id
        ))
        self.db_conn.commit()
        cursor.close()
        
    def process_message(self, message):
        """Process a single email message"""
        try:
            # Get full message
            msg = self.gmail_service.users().messages().get(
                userId='me',
                id=message['id'],
                format='full'
            ).execute()
            
            # Extract headers
            headers = msg['payload'].get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
            from_email = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown')
            date_str = next((h['value'] for h in headers if h['name'].lower() == 'date'), None)
            
            # Parse date
            email_date = None
            if date_str:
                try:
                    email_date = parsedate_to_datetime(date_str)
                except:
                    pass
            
            # Extract body
            body = self.extract_email_body(msg['payload'])
            
            print(f"\n  📨 {subject[:60]}...")
            print(f"     From: {from_email[:40]}")
            
            # Use Claude to extract property info
            property_data = self.process_email_with_claude(subject, body)
            
            if not property_data or not property_data.get('mls_number'):
                print(f"     ⚠️  No MLS found - skipping")
                self.skipped_count += 1
                return
            
            mls_number = property_data['mls_number']
            print(f"     🏠 MLS: {mls_number}")
            
            # Check if property exists
            property_id = self.check_property_exists(mls_number)
            
            if property_id:
                # Update existing property
                email_data = {
                    'subject': subject,
                    'body': body,
                    'from': from_email,
                    'date': email_date,
                    'gmail_message_id': message['id']
                }
                self.update_property_email(property_id, email_data)
                print(f"     ✅ Updated property ID {property_id}")
                self.updated_count += 1
            else:
                print(f"     ⚠️  Property not in database - skipping")
                self.skipped_count += 1
            
            self.processed_count += 1
            
        except Exception as e:
            print(f"     ❌ Error: {e}")
            self.skipped_count += 1
            
    def run(self):
        """Main execution"""
        try:
            print("=" * 60)
            print("🔄 REPROCESS EMAILS FROM INBOX")
            print("=" * 60)
            
            self.authenticate_gmail()
            self.connect_anthropic()
            self.connect_db()
            
            messages = self.get_property_emails()
            
            if not messages:
                print("\n✅ No property emails found")
                return
            
            print(f"\n🚀 Processing {len(messages)} emails...")
            print("   (This will take a few minutes)")
            
            for i, message in enumerate(messages, 1):
                print(f"\n[{i}/{len(messages)}]", end='')
                self.process_message(message)
                
                # Progress update every 10 emails
                if i % 10 == 0:
                    print(f"\n📊 Progress: {i}/{len(messages)} | Updated: {self.updated_count} | Skipped: {self.skipped_count}")
            
            # Final summary
            print("\n" + "=" * 60)
            print("✅ PROCESSING COMPLETE")
            print("=" * 60)
            print(f"📧 Total Processed: {self.processed_count}")
            print(f"✅ Properties Updated: {self.updated_count}")
            print(f"⚠️  Skipped: {self.skipped_count}")
            print("=" * 60)
            
            # Show updated properties
            cursor = self.db_conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) 
                FROM properties 
                WHERE email_body IS NOT NULL
            """)
            email_count = cursor.fetchone()[0]
            cursor.close()
            
            print(f"\n📊 Properties with email content: {email_count}")
            
        except Exception as e:
            print(f"\n❌ Fatal error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
            
        finally:
            if self.db_conn:
                self.db_conn.close()

if __name__ == '__main__':
    reprocessor = EmailReprocessor()
    reprocessor.run()
