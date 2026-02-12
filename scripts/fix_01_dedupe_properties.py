#!/usr/bin/env python3
"""
Fix 1: Dedupe Properties
- Finds duplicate properties by address
- Merges data (keeps most recent, preserves all related records)
- Does NOT add schema changes (column/constraints) - handled in email_processor instead
"""

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import os
import sys

# Add parent directory to path
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


def find_duplicates(conn):
    """Find all duplicate addresses"""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT 
            address,
            COUNT(*) as count,
            ARRAY_AGG(id ORDER BY updated_at DESC, created_at DESC) as property_ids
        FROM properties
        GROUP BY address
        HAVING COUNT(*) > 1
        ORDER BY count DESC
    """)
    return cur.fetchall()


def merge_properties(conn, keep_id, delete_ids):
    """
    Merge duplicate properties:
    - Keep the 'keep_id' record
    - Update all related records to point to keep_id
    - Delete the duplicate records
    """
    cur = conn.cursor()
    
    print(f"  Merging: keeping ID {keep_id}, deleting {delete_ids}")
    
    # Update related tables to point to the kept property
    for delete_id in delete_ids:
        # Update status_history
        cur.execute("""
            UPDATE status_history 
            SET property_id = %s 
            WHERE property_id = %s
        """, (keep_id, delete_id))
        
        # Update property_flags (only if keep_id doesn't have one)
        cur.execute("""
            UPDATE property_flags 
            SET property_id = %s 
            WHERE property_id = %s 
            AND NOT EXISTS (
                SELECT 1 FROM property_flags WHERE property_id = %s
            )
        """, (keep_id, delete_id, keep_id))
        
        # Delete duplicate property_flags if keep_id already has one
        cur.execute("""
            DELETE FROM property_flags 
            WHERE property_id = %s 
            AND EXISTS (
                SELECT 1 FROM property_flags WHERE property_id = %s
            )
        """, (delete_id, keep_id))
        
        # Update attachments
        cur.execute("""
            UPDATE attachments 
            SET property_id = %s 
            WHERE property_id = %s
        """, (keep_id, delete_id))
        
        # Update compliance_alerts
        cur.execute("""
            UPDATE compliance_alerts 
            SET property_id = %s 
            WHERE property_id = %s
        """, (keep_id, delete_id))
        
        # Update important_property_info
        cur.execute("""
            UPDATE important_property_info 
            SET property_id = %s 
            WHERE property_id = %s
        """, (keep_id, delete_id))
        
        # Update email_processing_log
        cur.execute("""
            UPDATE email_processing_log 
            SET property_id = %s 
            WHERE property_id = %s
        """, (keep_id, delete_id))
        
        # Update property_emails
        cur.execute("""
            UPDATE property_emails 
            SET property_id = %s 
            WHERE property_id = %s
        """, (keep_id, delete_id))
        
        # Delete the duplicate property
        cur.execute("DELETE FROM properties WHERE id = %s", (delete_id,))
        print(f"    Deleted property ID {delete_id}")
    
    conn.commit()


def main():
    print("=" * 60)
    print("FIX 1: DEDUPLICATE PROPERTIES")
    print("=" * 60)
    
    conn = get_connection()
    
    # Find duplicates
    duplicates = find_duplicates(conn)
    
    if not duplicates:
        print("\n✓ No duplicates found!")
    else:
        print(f"\n! Found {len(duplicates)} duplicate address groups")
        print("\nDuplicate addresses:")
        for dup in duplicates:
            print(f"  - {dup['address']}: {dup['count']} records (IDs: {dup['property_ids']})")
        
        # Ask for confirmation
        print("\n" + "=" * 60)
        response = input("Merge duplicates? This will keep the most recent record for each address. (yes/no): ")
        
        if response.lower() != 'yes':
            print("Aborted.")
            return
        
        # Merge each duplicate group
        for dup in duplicates:
            print(f"\nProcessing: {dup['address']}")
            keep_id = dup['property_ids'][0]  # Most recent (ordered by updated_at DESC)
            delete_ids = dup['property_ids'][1:]  # Older records
            
            try:
                merge_properties(conn, keep_id, delete_ids)
                print(f"  ✓ Merged successfully")
            except Exception as e:
                print(f"  ✗ Error: {e}")
                conn.rollback()
    
    # Verify no duplicates remain
    duplicates_after = find_duplicates(conn)
    print("\n" + "=" * 60)
    if duplicates_after:
        print(f"! WARNING: {len(duplicates_after)} duplicate groups still remain")
    else:
        print("✓ All duplicates have been merged")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("FIX 1 COMPLETE")
    print("Next step: Update email_processor.py to prevent future duplicates")
    print("=" * 60)


if __name__ == '__main__':
    main()

