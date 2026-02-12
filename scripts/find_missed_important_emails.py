#!/usr/bin/env python3
"""
Find Missed Important Emails
Searches for emails that were processed but not properly categorized:
- Highest & Best notifications
- Status Updates
- Multiple Offer situations
- Property condition updates
- Other critical emails with no property matched
"""

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import os
import sys
import re
from datetime import datetime

sys.path.insert(0, '/opt/island-realty')
load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        database=os.getenv('DB_NAME', 'island_properties'),
        user=os.getenv('DB_USER', 'island_user'),
        password=os.getenv('DB_PASSWORD'),
        port=os.getenv('DB_PORT', '5432')
    )


def extract_address_from_subject(subject):
    """Extract property address from email subject"""
    # Common patterns:
    # "Highest & Best Notification: 293 Avenue B Ronkonkoma NY 1177"
    # "Status Update: 299 South River Road Calverton NY 11933"
    # "Price Reduction: 825 Morrison Avenue Unit 12F Bronx NY 10473"
    
    patterns = [
        r':\s*(.+?)\s+([A-Z]{2})\s+(\d{5})',  # Standard: address STATE ZIP
        r':\s*(.+)',  # Fallback: everything after colon
    ]
    
    for pattern in patterns:
        match = re.search(pattern, subject, re.IGNORECASE)
        if match:
            if pattern == patterns[0]:  # Has state and zip
                address = match.group(1).strip()
                state = match.group(2)
                zip_code = match.group(3)
                return f"{address} {state} {zip_code}"
            else:
                return match.group(1).strip()
    
    return None


def smart_search_property(conn, address_from_subject):
    """Try to find matching property for an address from email subject"""
    if not address_from_subject:
        return None
    
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Extract street number for better matching
    street_num_match = re.match(r'^(\d+[\-/]?\d*)', address_from_subject)
    if not street_num_match:
        return None
    
    street_num = street_num_match.group(1)
    
    # Search for properties with matching street number
    cur.execute("""
        SELECT id, address
        FROM properties
        WHERE address ILIKE %s
        ORDER BY id DESC
        LIMIT 5
    """, (f'{street_num}%',))
    
    candidates = cur.fetchall()
    
    # Score each candidate
    best_match = None
    best_score = 0
    
    for prop in candidates:
        score = 0
        prop_addr = prop['address'].lower()
        search_addr = address_from_subject.lower()
        
        # Street number match (required)
        if prop_addr.startswith(street_num.lower()):
            score += 2
        
        # Check for common words
        search_words = set(search_addr.split())
        prop_words = set(prop_addr.split())
        common_words = search_words & prop_words
        score += len(common_words)
        
        if score > best_score:
            best_score = score
            best_match = prop['id']
    
    # Require minimum score of 3
    return best_match if best_score >= 3 else None


def find_highest_and_best_emails(conn):
    """Find all Highest & Best notification emails"""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cur.execute("""
        SELECT 
            email_id,
            email_subject,
            email_from,
            email_date,
            property_id,
            processing_status,
            actions_taken
        FROM email_processing_log
        WHERE (
            email_subject ILIKE '%highest%best%'
            OR email_subject ILIKE '%multiple offer%'
        )
        AND email_date >= '2026-01-01'
        ORDER BY email_date DESC
    """)
    
    return cur.fetchall()


def find_status_update_emails(conn):
    """Find status update emails that might have been missed"""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cur.execute("""
        SELECT 
            email_id,
            email_subject,
            email_from,
            email_date,
            property_id,
            processing_status,
            actions_taken
        FROM email_processing_log
        WHERE (
            email_subject ILIKE '%status update%'
            OR email_subject ILIKE '%1st accept%'
            OR email_subject ILIKE '%offer accepted%'
        )
        AND email_date >= '2026-01-01'
        ORDER BY email_date DESC
    """)
    
    return cur.fetchall()


def find_unmatched_property_emails(conn):
    """Find emails that mention properties but weren't matched"""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cur.execute("""
        SELECT 
            email_id,
            email_subject,
            email_from,
            email_date,
            property_id,
            processing_status,
            actions_taken,
            error_message
        FROM email_processing_log
        WHERE property_id IS NULL
        AND processing_status = 'success'
        AND (
            email_subject ~ '\\d{3,5}\\s+[A-Z]'  -- Has street number pattern
            OR email_subject ILIKE '%property%'
            OR email_subject ILIKE '%listing%'
            OR email_subject ILIKE '%address%'
        )
        AND email_date >= '2026-02-01'
        ORDER BY email_date DESC
        LIMIT 50
    """)
    
    return cur.fetchall()


def analyze_email_batch(emails, category_name):
    """Analyze a batch of emails and return statistics"""
    if not emails:
        return None
    
    stats = {
        'total': len(emails),
        'matched': sum(1 for e in emails if e['property_id'] is not None),
        'unmatched': sum(1 for e in emails if e['property_id'] is None),
        'with_attachments': 0,  # Would need to check property_emails table
        'emails': emails
    }
    
    return stats


def main():
    print("=" * 80)
    print("FINDING MISSED IMPORTANT EMAILS")
    print("=" * 80)
    
    conn = get_connection()
    
    # 1. Find Highest & Best emails
    print("\n1. HIGHEST & BEST NOTIFICATIONS:")
    print("-" * 80)
    hb_emails = find_highest_and_best_emails(conn)
    
    if not hb_emails:
        print("   ✓ No Highest & Best emails found")
    else:
        print(f"   Found {len(hb_emails)} Highest & Best emails\n")
        
        matched = 0
        unmatched = 0
        
        for email in hb_emails:
            is_matched = email['property_id'] is not None
            status_icon = "✓" if is_matched else "✗"
            
            if is_matched:
                matched += 1
            else:
                unmatched += 1
            
            print(f"   {status_icon} {email['email_subject']}")
            print(f"      From: {email['email_from']}")
            print(f"      Date: {email['email_date']}")
            print(f"      Property ID: {email['property_id'] or 'NOT MATCHED'}")
            
            # Try to find matching property if not matched
            if not is_matched:
                address = extract_address_from_subject(email['email_subject'])
                if address:
                    print(f"      Extracted address: {address}")
                    potential_match = smart_search_property(conn, address)
                    if potential_match:
                        print(f"      → Potential match: Property ID {potential_match}")
                    else:
                        print(f"      → No matching property found - NEEDS MANUAL CREATION")
            print()
        
        print(f"   Summary: {matched} matched, {unmatched} unmatched")
    
    # 2. Find Status Update emails
    print("\n2. STATUS UPDATE EMAILS:")
    print("-" * 80)
    status_emails = find_status_update_emails(conn)
    
    if not status_emails:
        print("   ✓ No status update emails found")
    else:
        print(f"   Found {len(status_emails)} status update emails\n")
        
        for email in status_emails:
            is_matched = email['property_id'] is not None
            status_icon = "✓" if is_matched else "✗"
            
            print(f"   {status_icon} {email['email_subject']}")
            print(f"      Property ID: {email['property_id'] or 'NOT MATCHED'}")
            
            if not is_matched:
                address = extract_address_from_subject(email['email_subject'])
                if address:
                    potential_match = smart_search_property(conn, address)
                    if potential_match:
                        print(f"      → Suggested match: Property ID {potential_match}")
            print()
    
    # 3. Find other unmatched property emails
    print("\n3. OTHER UNMATCHED PROPERTY EMAILS:")
    print("-" * 80)
    unmatched_emails = find_unmatched_property_emails(conn)
    
    if not unmatched_emails:
        print("   ✓ No unmatched property emails found")
    else:
        print(f"   Found {len(unmatched_emails)} potentially important unmatched emails\n")
        
        important_count = 0
        
        for email in unmatched_emails:
            # Filter out non-property emails
            subject_lower = email['email_subject'].lower()
            
            # Skip non-property emails
            skip_keywords = ['sign company', 'open house training', 'office closed', 
                           'manditory', 'update on', 'presidents day']
            if any(kw in subject_lower for kw in skip_keywords):
                continue
            
            important_count += 1
            
            print(f"   ✗ {email['email_subject']}")
            print(f"      From: {email['email_from']}")
            print(f"      Date: {email['email_date']}")
            
            address = extract_address_from_subject(email['email_subject'])
            if address:
                print(f"      Address: {address}")
                potential_match = smart_search_property(conn, address)
                if potential_match:
                    print(f"      → Suggested match: Property ID {potential_match}")
                else:
                    print(f"      → NEEDS NEW PROPERTY CREATED")
            else:
                print(f"      → Could not extract address from subject")
            print()
        
        if important_count == 0:
            print("   ✓ No important unmatched emails (filtered out non-property emails)")
    
    # 4. Summary and recommendations
    print("\n" + "=" * 80)
    print("SUMMARY & RECOMMENDATIONS")
    print("=" * 80)
    
    total_hb = len(hb_emails) if hb_emails else 0
    total_status = len(status_emails) if status_emails else 0
    total_unmatched = len([e for e in unmatched_emails if e['property_id'] is None]) if unmatched_emails else 0
    
    print(f"\nTotal emails reviewed: {total_hb + total_status + total_unmatched}")
    print(f"  - Highest & Best: {total_hb}")
    print(f"  - Status Updates: {total_status}")
    print(f"  - Other unmatched: {total_unmatched}")
    
    # Count unmatched
    unmatched_hb = len([e for e in hb_emails if e['property_id'] is None]) if hb_emails else 0
    unmatched_status = len([e for e in status_emails if e['property_id'] is None]) if status_emails else 0
    
    print(f"\nUnmatched emails requiring attention: {unmatched_hb + unmatched_status}")
    
    if unmatched_hb + unmatched_status > 0:
        print("\n⚠️  ACTION REQUIRED:")
        print("   1. Review emails marked with ✗ above")
        print("   2. Create missing properties or link to existing ones")
        print("   3. Update email_processor.py to recognize these email types")
        print("   4. Retrieve attachments from these emails")
    else:
        print("\n✓ All important emails are properly matched!")
    
    conn.close()
    
    print("\n" + "=" * 80)
    print("SCAN COMPLETE")
    print("=" * 80)


if __name__ == '__main__':
    main()

