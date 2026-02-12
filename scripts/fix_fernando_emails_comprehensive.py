#!/usr/bin/env python3
"""
COMPREHENSIVE FIX: Fernando Email Matching & Price Preservation
Addresses all root causes:
1. Fix bad matches from retroactive script
2. Create missing properties
3. Implement price preservation
4. Deploy enhanced matching logic
"""

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import os
import sys
import re

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


def find_mismatched_emails(conn):
    """
    Find emails that were matched to obviously wrong properties
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    print("\n" + "=" * 60)
    print("FINDING MISMATCHED EMAILS")
    print("=" * 60)
    
    # Get emails with "New List Price" that have addresses in subject
    cur.execute("""
        SELECT 
            epl.email_id,
            epl.email_subject,
            epl.email_date,
            epl.property_id,
            p.address as matched_address,
            epl.actions_taken
        FROM email_processing_log epl
        LEFT JOIN properties p ON epl.property_id = p.id
        WHERE epl.email_subject ILIKE '%new list price%'
        AND epl.email_date >= '2026-02-01'
        ORDER BY epl.email_date DESC
    """)
    emails = cur.fetchall()
    
    mismatches = []
    
    for email in emails:
        # Extract address from subject
        subject = email['email_subject']
        match = re.search(r'New List Price:\s*(.+?)(?:\s+NY\s+\d{5})?$', subject, re.IGNORECASE)
        if match:
            subject_address = match.group(1).strip()
            matched_address = email['matched_address'] if email['matched_address'] else 'NO PROPERTY'
            
            # Check if they match
            subject_norm = ' '.join(subject_address.lower().split())
            matched_norm = ' '.join(matched_address.lower().split()) if matched_address != 'NO PROPERTY' else ''
            
            # Extract street numbers for comparison
            subject_num = re.search(r'^(\d+[\-/]?\d*)', subject_norm)
            matched_num = re.search(r'^(\d+[\-/]?\d*)', matched_norm) if matched_norm else None
            
            is_mismatch = False
            if email['property_id'] is None:
                is_mismatch = True  # No property created
            elif subject_num and matched_num:
                # Street numbers must match
                if subject_num.group(1) != matched_num.group(1):
                    is_mismatch = True
            elif subject_address[:20].lower() not in matched_address[:40].lower():
                is_mismatch = True
            
            if is_mismatch:
                mismatches.append({
                    'email_id': email['email_id'],
                    'subject': subject,
                    'subject_address': subject_address,
                    'property_id': email['property_id'],
                    'matched_address': matched_address,
                    'email_date': email['email_date']
                })
    
    print(f"\nFound {len(mismatches)} mismatched emails:\n")
    for m in mismatches:
        print(f"Email: {m['subject']}")
        print(f"  Subject address: {m['subject_address']}")
        print(f"  Matched to: {m['matched_address']} (ID: {m['property_id']})")
        print(f"  Date: {m['email_date']}")
        print()
    
    return mismatches


def create_missing_property(conn, address, email_id, email_subject, email_date):
    """
    Create a property from email data
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Parse address into components
    # Pattern: "293 Avenue B Ronkonkoma NY 11779"
    match = re.match(r'^(.+?)\s+([A-Z][a-z\s]+)\s+NY\s+(\d{5})', address)
    
    if match:
        street = match.group(1).strip()
        city = match.group(2).strip()
        zip_code = match.group(3)
        full_address = f"{street}, {city}, NY {zip_code}"
    else:
        full_address = address
        city = None
        zip_code = None
    
    # Check if already exists
    cur.execute("""
        SELECT id FROM properties 
        WHERE address ILIKE %s
    """, (f'%{full_address}%',))
    existing = cur.fetchone()
    
    if existing:
        print(f"  Property already exists: ID {existing['id']}")
        return existing['id']
    
    # Create new property
    cur.execute("""
        INSERT INTO properties
        (address, current_status, data_source, last_email_id,
         email_subject, email_date, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
        RETURNING id
    """, (full_address, 'Available', 'email', email_id, email_subject, email_date))
    
    new_property_id = cur.fetchone()['id']
    conn.commit()
    
    print(f"  ✓ Created property ID {new_property_id}: {full_address}")
    return new_property_id


def fix_mismatch(conn, mismatch):
    """
    Fix a single mismatched email
    """
    cur = conn.cursor()
    
    print(f"\nFixing: {mismatch['subject_address']}")
    
    # Create the correct property
    property_id = create_missing_property(
        conn,
        mismatch['subject_address'],
        mismatch['email_id'],
        mismatch['subject'],
        mismatch['email_date']
    )
    
    # Update email_processing_log
    cur.execute("""
        UPDATE email_processing_log
        SET property_id = %s,
            actions_taken = '["mismatch_fixed", "property_created"]'
        WHERE email_id = %s
    """, (property_id, mismatch['email_id']))
    
    # Update property_emails if exists
    cur.execute("""
        UPDATE property_emails
        SET property_id = %s
        WHERE gmail_message_id = %s
    """, (property_id, mismatch['email_id']))
    
    conn.commit()
    print(f"  ✓ Re-associated email to property {property_id}")


def implement_price_preservation(conn):
    """
    Add logic to preserve original prices
    Update any properties where original_list_price is NULL but we have history
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    print("\n" + "=" * 60)
    print("IMPLEMENTING PRICE PRESERVATION")
    print("=" * 60)
    
    # Find properties missing original_list_price
    cur.execute("""
        SELECT id, address, current_list_price, original_list_price
        FROM properties
        WHERE current_list_price IS NOT NULL
        AND original_list_price IS NULL
    """)
    missing_original = cur.fetchall()
    
    print(f"\nFound {len(missing_original)} properties missing original_list_price")
    
    # Set original = current for these
    for prop in missing_original:
        cur.execute("""
            UPDATE properties
            SET original_list_price = current_list_price
            WHERE id = %s
        """, (prop['id'],))
    
    conn.commit()
    print(f"✓ Set original_list_price for {len(missing_original)} properties")


def create_price_update_trigger(conn):
    """
    Create database trigger to preserve original price
    """
    cur = conn.cursor()
    
    print("\nCreating price preservation trigger...")
    
    # Create function
    cur.execute("""
        CREATE OR REPLACE FUNCTION preserve_original_price()
        RETURNS TRIGGER AS $$
        BEGIN
            -- If original_list_price is NULL, set it to the first non-null current_list_price
            IF NEW.current_list_price IS NOT NULL AND 
               (OLD.original_list_price IS NULL OR NEW.original_list_price IS NULL) THEN
                NEW.original_list_price := COALESCE(NEW.original_list_price, OLD.original_list_price, NEW.current_list_price);
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Create trigger
    cur.execute("""
        DROP TRIGGER IF EXISTS trigger_preserve_original_price ON properties;
        
        CREATE TRIGGER trigger_preserve_original_price
        BEFORE UPDATE ON properties
        FOR EACH ROW
        EXECUTE FUNCTION preserve_original_price();
    """)
    
    conn.commit()
    print("✓ Price preservation trigger created")


def verify_fixes(conn):
    """
    Verify all fixes worked
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)
    
    # Check for missing properties
    print("\n1. Checking for required properties...")
    required = [
        '293 Avenue B',
        '160 Beach 30th',
        '140 Arlington'
    ]
    
    for addr in required:
        cur.execute("SELECT id, address, current_status FROM properties WHERE address ILIKE %s", (f'%{addr}%',))
        prop = cur.fetchone()
        if prop:
            print(f"   ✓ {addr}: ID {prop['id']}, Status: {prop['current_status']}")
        else:
            print(f"   ❌ {addr}: NOT FOUND")
    
    # Check price preservation
    print("\n2. Checking price preservation...")
    cur.execute("""
        SELECT COUNT(*) as count
        FROM properties
        WHERE current_list_price IS NOT NULL
        AND original_list_price IS NULL
    """)
    missing = cur.fetchone()['count']
    if missing == 0:
        print(f"   ✓ All properties with prices have original_list_price set")
    else:
        print(f"   ⚠️  {missing} properties still missing original_list_price")
    
    # Check for recent mismatches
    print("\n3. Checking for recent bad matches...")
    cur.execute("""
        SELECT COUNT(*) as count
        FROM email_processing_log
        WHERE actions_taken LIKE '%retroactive_match%'
        AND email_date >= '2026-02-01'
    """)
    retro = cur.fetchone()['count']
    print(f"   Found {retro} emails with retroactive_match actions")


def main():
    print("=" * 60)
    print("COMPREHENSIVE FIX: EMAIL MATCHING & PRICE PRESERVATION")
    print("=" * 60)
    
    conn = get_connection()
    
    # Step 1: Find mismatched emails
    mismatches = find_mismatched_emails(conn)
    
    if not mismatches:
        print("\n✓ No mismatches found!")
    else:
        # Ask for confirmation
        print("\n" + "=" * 60)
        response = input(f"Fix {len(mismatches)} mismatched emails? (yes/no): ")
        
        if response.lower() == 'yes':
            # Step 2: Fix each mismatch
            print("\n" + "=" * 60)
            print("FIXING MISMATCHES")
            print("=" * 60)
            
            for mismatch in mismatches:
                try:
                    fix_mismatch(conn, mismatch)
                except Exception as e:
                    print(f"  ✗ Error: {e}")
            
            print(f"\n✓ Fixed {len(mismatches)} mismatches")
    
    # Step 3: Implement price preservation
    implement_price_preservation(conn)
    create_price_update_trigger(conn)
    
    # Step 4: Verify
    verify_fixes(conn)
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("FIX COMPLETE")
    print("\nNext steps:")
    print("1. Deploy enhanced_email_functions.py to monitor_email_v4.py")
    print("2. Restart email monitor service")
    print("3. Test with new email")
    print("=" * 60)


if __name__ == '__main__':
    main()

