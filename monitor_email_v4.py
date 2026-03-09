#!/usr/bin/env python3
"""
Island Advantage Property Management System V4
Enhanced Email Monitor with AI Processing
FIXED: Removed is:unread dependency, added attachment tracking, FOIL detection
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Add app directory to path
sys.path.insert(0, '/opt/island-realty/app')

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import base64
from email.mime.text import MIMEText
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from bs4 import BeautifulSoup

# Import our modules
from models import EmailProcessingLog, db_connection
from email_processor import EmailProcessor

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/island-realty/logs/email_monitor_v4.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Also log errors to separate file
error_handler = logging.FileHandler('/opt/island-realty/logs/email_monitor_error.log')
error_handler.setLevel(logging.INFO)
error_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(error_handler)

# Gmail API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.modify']


def _normalize_status(s):
    """Normalize raw status strings (from AI or spreadsheet) to canonical form.
    Used in both step 5 (status update) and step 6 (price preservation check)
    so variants like '1st Accept' are always treated as 'First Accepted'.
    NOTE: 'Price Reduced' is not a valid status - price reductions only update the price,
    they do not change the status. Any 'Price Reduced' value is mapped to None (no status change).
    """
    if not s:
        return s
    sl = s.lower().strip()
    if sl in ('pending', 'under contract', 'pended', 'in contract'):
        return 'In Contract'
    if sl in ('available', 'lpp', 'auction/available', 'auction available'):
        return 'Auction Available'
    if sl in ('1st accept', '1st accepted', 'first accepted'):
        return 'First Accepted'
    if sl in ('t-o-t-m', 'temporarily off the market', 'totm'):
        return 'TOTM'
    if sl in ('highest and best', 'highest & best'):
        return 'Highest & Best'
    if sl in ('price reduced', 'price reduction', 'reduced'):
        return None  # Price reductions: update price only, keep existing status
    return s


class EmailMonitorV4:
    """Enhanced email monitor with AI processing for V4"""
    
    def __init__(self):
        self.service = None
        self.processor = EmailProcessor(os.getenv("ANTHROPIC_API_KEY"))
        self.processed_emails = set()
        
        # Load environment variables
        self.credentials_path = '/opt/island-realty/config/gmail-credentials.json'
        self.token_path = '/opt/island-realty/config/token.pickle'
        
    def authenticate(self):
        """Authenticate with Gmail API using existing pickle token"""
        import pickle
        
        if not os.path.exists(self.token_path):
            logger.error(f"Token file not found: {self.token_path}")
            raise FileNotFoundError(f"Token file not found: {self.token_path}")
        
        with open(self.token_path, 'rb') as token:
            creds = pickle.load(token)
        
        self.service = build('gmail', 'v1', credentials=creds)
        logger.info("Gmail authentication successful")
        
    def get_unprocessed_emails(self, max_results=100):
        """Get property-related emails, using DB processing log as source of truth.
        
        FIX: Removed is:unread filter. Now fetches recent emails and checks
        the email_processing_log table to determine which are new.
        """
        try:
            # Search for unread property emails from last 30 days
            query_parts = [
                'is:unread',
                f'after:{int((datetime.now() - timedelta(days=30)).timestamp())}'
            ]
            
            # Broader keyword search to catch REO-specific terminology
            keywords = [
                'Status Update', 'New List Price', 'Price Reduction', 'Price Reduced',
                'Highest & Best', 'Highest and Best', 'TOTM', 'First Accepted',
                'Back on the Market', 'BOM', 'Auction Available', 'Temporarily off',
                'Origination', 'New Listing', 'Back on Market', 'Contract', 'T-O-T-M', 'Temporarily off the Market'
            ]
            keyword_query = ' OR '.join([f'subject:"{kw}"' for kw in keywords])
            query_parts.append(f'({keyword_query})')
            
            query = ' '.join(query_parts)
            
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            messages = results.get('messages', [])
            logger.info(f"Found {len(messages)} matching emails from Gmail")
            
            # Filter out already-processed emails using DB
            unprocessed = []
            for msg in messages:
                if not EmailProcessingLog.is_processed(msg['id']):
                    unprocessed.append(msg)
            
            logger.info(f"Of those, {len(unprocessed)} are unprocessed (new)")
            
            # Also try to mark already-processed emails as read to keep inbox clean
            processed_count = len(messages) - len(unprocessed)
            if processed_count > 0:
                self._mark_processed_as_read(messages, unprocessed)
            
            return unprocessed
            
        except HttpError as error:
            logger.error(f"Error fetching emails: {error}")
            return []
    
    def _mark_processed_as_read(self, all_messages, unprocessed):
        """Mark already-processed emails as read to keep Gmail clean"""
        unprocessed_ids = {m['id'] for m in unprocessed}
        marked = 0
        for msg in all_messages:
            if msg['id'] not in unprocessed_ids:
                try:
                    self.service.users().messages().modify(
                        userId='me',
                        id=msg['id'],
                        body={'removeLabelIds': ['UNREAD']}
                    ).execute()
                    marked += 1
                except Exception:
                    pass  # Non-critical, just cleanup
        if marked > 0:
            logger.info(f"Cleaned up {marked} already-processed emails (marked as read)")
    
    def get_email_content(self, message_id):
        """Get full email content including body and attachments"""
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            
            # Extract headers
            headers = message['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
            from_email = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown')
            date_str = next((h['value'] for h in headers if h['name'].lower() == 'date'), None)
            
            # Extract body
            body = self._extract_body(message['payload'])
            
            # Extract attachments info (with gmail_attachment_id for later retrieval)
            attachments = self._get_attachment_info(message['payload'], message_id)
            
            return {
                'id': message_id,
                'subject': subject,
                'from': from_email,
                'date': date_str,
                'body': body,
                'attachments': attachments,
                'raw_message': message
            }
            
        except HttpError as error:
            logger.error(f"Error fetching email content for {message_id}: {error}")
            return None
    
    def _extract_body(self, payload):
        """Extract email body from payload, handling nested MIME parts"""
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
                    # Recurse into nested multipart
                    body += self._extract_body(part)
        else:
            data = payload['body'].get('data', '')
            if data:
                body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
        
        return body
    
    def _get_attachment_info(self, payload, message_id, depth=0):
        """Get attachment information including gmail_attachment_id for retrieval.
        Recursively handles nested MIME parts."""
        attachments = []
        
        if 'parts' in payload:
            for part in payload['parts']:
                filename = part.get('filename', '')
                if filename:
                    attachment_id = part['body'].get('attachmentId')
                    is_foil = 'foil' in filename.lower()
                    
                    # PDF-only policy: skip non-PDF attachments
                    mime = part.get('mimeType', '')
                    if not (mime == 'application/pdf' or filename.lower().endswith('.pdf')):
                        logger.info(f"  ⏭️ Skipping non-PDF attachment: {filename} ({mime})")
                        continue
                    
                    attachment = {
                        'filename': filename,
                        'mimeType': mime or 'application/pdf',
                        'size': part['body'].get('size', 0),
                        'attachmentId': attachment_id,
                        'gmail_message_id': message_id,
                        'is_foil': is_foil
                    }
                    attachments.append(attachment)
                    
                    if is_foil:
                        logger.info(f"  🔴 FOIL attachment detected: {filename}")
                
                # Recurse into nested parts
                if 'parts' in part and depth < 5:
                    attachments.extend(self._get_attachment_info(part, message_id, depth + 1))
        
        return attachments
    
    def process_email(self, email_data):
        """Process a single email with AI extraction"""
        email_id = email_data['id']
        
        # Double-check not already processed
        if EmailProcessingLog.is_processed(email_id):
            logger.info(f"Email {email_id} already processed, skipping")
            return None
        
        logger.info(f"Processing email: {email_data['subject']}")
        
        start_time = time.time()
        
        try:
            # Use AI to extract property data
            extracted_data = self.processor.process_email({
                "subject": email_data['subject'],
                "body": email_data['body'],
                "attachments": [a['filename'] for a in email_data['attachments']]
            })
            
            processing_time = int((time.time() - start_time) * 1000)
            
            if extracted_data:
                # Save to database (including real attachment data)
                result = self._save_to_database(email_data, extracted_data)
                
                # Log successful processing
                EmailProcessingLog.log(
                    email_id=email_id,
                    email_subject=email_data['subject'],
                    email_from=email_data['from'],
                    email_date=email_data['date'],
                    status='success',
                    property_id=result.get('property_id'),
                    actions_taken=json.dumps(result.get('actions')),
                    processing_time_ms=processing_time,
                    ai_model_used='claude-sonnet-4'
                )
                
                # Mark as read
                self._mark_as_read(email_id)
                
                logger.info(f"Successfully processed email {email_id} in {processing_time}ms - actions: {result.get('actions')}")
                return result
            else:
                # No property data found
                EmailProcessingLog.log(
                    email_id=email_id,
                    email_subject=email_data['subject'],
                    email_from=email_data['from'],
                    email_date=email_data['date'],
                    status='no_property_data',
                    processing_time_ms=processing_time,
                    ai_model_used='claude-sonnet-4'
                )
                
                self._mark_as_read(email_id)
                return None
                
        except Exception as e:
            processing_time = int((time.time() - start_time) * 1000)
            logger.error(f"Error processing email {email_id}: {str(e)}", exc_info=True)
            
            EmailProcessingLog.log(
                email_id=email_id,
                email_subject=email_data['subject'],
                email_from=email_data['from'],
                email_date=email_data['date'],
                status='error',
                error_message=str(e),
                processing_time_ms=processing_time,
                ai_model_used='claude-sonnet-4'
            )
            
            # Still mark as read so it doesn't block the queue
            self._mark_as_read(email_id)
            return None
    
    def _save_to_database(self, email_data, extracted_data):
        """Save extracted data to V4 database, including real attachment tracking"""
        actions = []
        property_id = None
        
        with db_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            try:
                property_data = extracted_data.get('property_data', {})
                
                if not property_data:
                    return {'property_id': None, 'actions': ['no_property_data']}
                
                # 1. Find or create property
                mls_number = property_data.get('mls_number')
                address = property_data.get('address')
                
                if mls_number:
                    cursor.execute("SELECT id FROM properties WHERE mls_number = %s", (mls_number,))
                    row = cursor.fetchone()
                    if row:
                        property_id = row['id']
                        actions.append(f'found_existing_mls_{property_id}')
                    else:
                        property_id = self._create_property(cursor, property_data, email_data)
                        actions.append(f'created_property_{property_id}')
                elif address:
                    cursor.execute("SELECT id FROM properties WHERE LOWER(address) LIKE LOWER(%s) LIMIT 1", (f'%{address}%',))
                    row = cursor.fetchone()
                    if row:
                        property_id = row['id']
                        actions.append(f'found_existing_addr_{property_id}')
                    else:
                        property_id = self._create_property(cursor, property_data, email_data)
                        actions.append(f'created_property_{property_id}')
                
                if not property_id:
                    conn.commit()
                    return {'property_id': None, 'actions': ['no_property_created']}
                
                # 2. Link email to property via property_emails table
                has_attachments = len(email_data.get('attachments', [])) > 0
                attachment_count = len(email_data.get('attachments', []))
                attachment_names = [a['filename'] for a in email_data.get('attachments', [])]
                
                cursor.execute("""
                    INSERT INTO property_emails 
                    (property_id, gmail_message_id, email_subject, email_body, email_from, email_date,
                     has_attachments, attachment_count, attachment_names)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (gmail_message_id) DO NOTHING
                """, (property_id, email_data['id'], email_data['subject'], 
                      email_data['body'], email_data['from'], email_data['date'],
                      has_attachments, attachment_count, attachment_names))
                actions.append('email_linked')
                
                # 3. Save REAL attachments with gmail_attachment_id for later retrieval
                any_foil = False
                for att in email_data.get('attachments', []):
                    is_foil = att.get('is_foil', False)
                    if is_foil:
                        any_foil = True
                    
                    # Determine category from AI extraction or filename
                    category = 'General'
                    ai_attachments = extracted_data.get('attachments', [])
                    for ai_att in ai_attachments:
                        if ai_att.get('filename') == att['filename']:
                            category = ai_att.get('category', 'General')
                            break
                    
                    # Check filename patterns for category
                    fname_lower = att['filename'].lower()
                    if 'foil' in fname_lower:
                        category = 'FOIL'
                    elif any(x in fname_lower for x in ['violation', 'ecb']):
                        category = 'Violations'
                    elif any(x in fname_lower for x in ['co ', 'tco', 'certificate']):
                        category = 'CO/TCO'
                    
                    cursor.execute("""
                        INSERT INTO attachments 
                        (property_id, filename, file_size, mime_type, category, 
                         source_email_id, gmail_attachment_id, gmail_message_id, is_foil,
                         source_email_date, uploaded_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (property_id, att['filename'], att.get('size', 0), 
                          att.get('mimeType', 'application/octet-stream'),
                          category, email_data['id'], att.get('attachmentId'),
                          att.get('gmail_message_id', email_data['id']),
                          is_foil, email_data['date'], 'email_monitor'))
                    
                    actions.append(f'attachment_saved:{att["filename"]}')
                    if is_foil:
                        actions.append(f'FOIL_detected:{att["filename"]}')
                
                # 4. Update property attachment counts
                if attachment_count > 0:
                    cursor.execute("""
                        UPDATE properties SET 
                            has_attachments = TRUE,
                            attachment_count = (
                                SELECT COUNT(*) FROM attachments WHERE property_id = %s
                            ),
                            last_email_id = %s,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (property_id, email_data['id'], property_id))
                
                # 5. Handle status change (with price protection & TOTM support)
                # NOTE: _normalize_status() is a module-level function available here and in step 6
                status_change = extracted_data.get('status_change')
                normalized_new_status = None  # will be set below if status_change present
                if status_change and status_change.get('new_status'):
                    normalized_new_status = _normalize_status(status_change['new_status'])
                    cursor.execute("SELECT current_status, current_list_price FROM properties WHERE id = %s", (property_id,))
                    current = cursor.fetchone()
                    old_status = current['current_status'] if current else None
                    
                    if old_status != normalized_new_status:
                        # TOTM handling: record when property went TOTM
                        if normalized_new_status == 'TOTM':
                            cursor.execute("""
                                UPDATE properties SET 
                                    current_status = %s, 
                                    totm_since = NOW(),
                                    updated_at = NOW(),
                                    last_email_id = %s
                                WHERE id = %s
                            """, (normalized_new_status, email_data['id'], property_id))
                            logger.info(f"Property {property_id} set to TOTM - hidden from public view")
                        
                        # Back on Market from TOTM: restore to Active, clear TOTM date, keep last price
                        elif normalized_new_status == 'Active' and old_status == 'TOTM':
                            cursor.execute("""
                                UPDATE properties SET 
                                    current_status = %s, 
                                    totm_since = NULL,
                                    updated_at = NOW(),
                                    last_email_id = %s
                                WHERE id = %s
                            """, (normalized_new_status, email_data['id'], property_id))
                            logger.info(f"Property {property_id} back from TOTM -> Active (price preserved)")
                        
                        else:
                            # Standard status update - clear TOTM date if set
                            cursor.execute("""
                                UPDATE properties SET 
                                    current_status = %s,
                                    totm_since = NULL,
                                    updated_at = NOW(),
                                    last_email_id = %s
                                WHERE id = %s
                            """, (normalized_new_status, email_data['id'], property_id))
                        
                        # Log status history
                        cursor.execute("""
                            INSERT INTO status_history 
                            (property_id, old_status, new_status, source_email_id, source_email_subject)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (property_id, old_status, normalized_new_status, email_data['id'], email_data['subject']))
                        
                        actions.append(f'status:{old_status}->{normalized_new_status}')
                
                # 6. Update price if changed
                # RULE: Price must always remain populated for every property.
                # When status changes to First Accepted (or any status-only email):
                #   - If email has a price  → update price normally
                #   - If email has NO price → do NOT touch price; keep the most recent known price
                # This uses normalized_new_status (set in step 5) so variants like '1st Accept'
                # are correctly handled — never raw/unnormalized status from AI output.
                new_price = property_data.get('current_list_price')
                
                if new_price and new_price > 0:
                    # Email contains a valid price — update it
                    cursor.execute("""
                        UPDATE properties SET 
                            current_list_price = %s,
                            updated_at = NOW()
                        WHERE id = %s AND (current_list_price IS NULL OR current_list_price != %s)
                    """, (new_price, property_id, new_price))
                    if cursor.rowcount > 0:
                        actions.append(f'price_updated:{new_price}')
                else:
                    # No price in this email — explicitly preserve the existing price.
                    # Do NOT set price to null/0. The existing DB value stays as-is.
                    # This is the correct behavior for First Accepted, TOTM, H&B, and all
                    # status-only notification emails that don't carry a price.
                    if normalized_new_status:
                        actions.append(f'price_preserved_on_{normalized_new_status}')
                        logger.info(
                            f"Property {property_id}: price preserved (no price in email, "
                            f"status={normalized_new_status})"
                        )
                
                # 7. Handle Highest & Best deadline
                highest_best = extracted_data.get('highest_best', {})
                hb_due_date = highest_best.get('due_date') if highest_best else None
                hb_due_time = highest_best.get('due_time') if highest_best else None
                if hb_due_date:
                    try:
                        from datetime import datetime as dt_class
                        if hb_due_time:
                            due_at = dt_class.strptime(f"{hb_due_date} {hb_due_time}", "%Y-%m-%d %H:%M")
                        else:
                            due_at = dt_class.strptime(hb_due_date, "%Y-%m-%d").replace(hour=23, minute=59)
                        
                        cursor.execute("""
                            UPDATE properties SET highest_best_due_at = %s, updated_at = NOW()
                            WHERE id = %s
                        """, (due_at, property_id))
                        
                        # Also save to deadlines table
                        cursor.execute("""
                            UPDATE highest_best_deadlines SET is_active = FALSE, expired_at = NOW()
                            WHERE property_id = %s AND is_active = TRUE
                        """, (property_id,))
                        cursor.execute("""
                            INSERT INTO highest_best_deadlines 
                            (property_id, due_date, due_time, due_at, offer_rules, submission_instructions, source_email_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (property_id, hb_due_date, hb_due_time, due_at,
                                highest_best.get('offer_rules'), highest_best.get('submission_instructions'),
                                email_data['id']))
                        
                        actions.append(f'highest_best_due:{due_at}')
                    except Exception as hb_err:
                        logger.warning(f"Failed to parse H&B date: {hb_err}")
                
                conn.commit()
                
                return {
                    'property_id': property_id,
                    'actions': actions,
                    'has_foil': any_foil
                }
                
            except Exception as e:
                conn.rollback()
                logger.error(f"Database error: {str(e)}", exc_info=True)
                raise
    
    def _create_property(self, cursor, property_data, email_data):
        """Create a new property record"""
        cursor.execute("""
            INSERT INTO properties 
            (mls_number, address, city, zip_code, property_type, 
             current_list_price, original_list_price, current_status, 
             data_source, last_email_id, email_subject, email_from, email_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (mls_number) DO UPDATE SET
                address = COALESCE(EXCLUDED.address, properties.address),
                updated_at = NOW()
            RETURNING id
        """, (
            property_data.get('mls_number'),
            property_data.get('address'),
            property_data.get('city'),
            property_data.get('zip_code'),
            property_data.get('property_type'),
            property_data.get('current_list_price'),
            property_data.get('original_list_price'),
            property_data.get('current_status', 'Active'),
            'email',
            email_data['id'],
            email_data['subject'],
            email_data['from'],
            email_data['date']
        ))
        row = cursor.fetchone()
        return row['id'] if row else None
    
    def _mark_as_read(self, message_id):
        """Mark email as read in Gmail"""
        try:
            self.service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()
        except HttpError as error:
            logger.warning(f"Failed to mark email {message_id} as read: {error}")
        except Exception as error:
            logger.warning(f"Unexpected error marking email as read: {error}")
    
    def run_cycle(self):
        """Run one monitoring cycle"""
        logger.info("Starting email monitoring cycle")
        
        try:
            # Get unprocessed emails
            messages = self.get_unprocessed_emails()
            
            if not messages:
                logger.info("No new emails to process")
                return
            
            # Process each email
            processed_count = 0
            for message in messages:
                email_data = self.get_email_content(message['id'])
                if email_data:
                    result = self.process_email(email_data)
                    if result:
                        processed_count += 1
            
            logger.info(f"Processed {processed_count} new emails this cycle")
            
        except Exception as e:
            logger.error(f"Error in monitoring cycle: {str(e)}", exc_info=True)
    
    def run_continuous(self, interval_minutes=5):
        """Run continuous monitoring"""
        logger.info(f"Starting continuous monitoring (interval: {interval_minutes} minutes)")
        
        while True:
            try:
                self.run_cycle()
                time.sleep(interval_minutes * 60)
                
            except KeyboardInterrupt:
                logger.info("Monitoring stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in continuous monitoring: {str(e)}", exc_info=True)
                time.sleep(60)  # Sleep 1 minute on error

def main():
    """Main entry point"""
    monitor = EmailMonitorV4()
    
    try:
        monitor.authenticate()
        monitor.run_continuous(interval_minutes=5)
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()

