#!/usr/bin/env python3
"""
Property Deduplication Script
Merges duplicate property records, preserving all data
"""

import sys
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import difflib

def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="island_properties",
        user="island_user",
        password="island123!"
    )

def normalize_address(address):
    """Normalize address for comparison"""
    if not address:
        return ""
    # Remove extra spaces, convert to lowercase
    normalized = ' '.join(address.lower().split())
    # Remove common variations
    normalized = normalized.replace(' street', ' st')
    normalized = normalized.replace(' avenue', ' ave')
    normalized = normalized.replace(' road', ' rd')
    normalized = normalized.replace(' boulevard', ' blvd')
    return normalized

def find_duplicates(cursor):
    """Find duplicate properties by address"""
    # Get all properties with their normalized addresses
    cursor.execute("""
        SELECT id, address, mls_number, created_at, updated_at,
               current_list_price, current_status,
               (SELECT COUNT(*) FROM attachments WHERE property_id = properties.id) as attachment_count
        FROM properties
        WHERE address IS NOT NULL
        ORDER BY address, created_at
    """)
    
    properties = cursor.fetchall()
    
    # Group by normalized address
    address_groups = {}
    for prop in properties:
        norm_addr = normalize_address(prop['address'])
        if norm_addr not in address_groups:
            address_groups[norm_addr] = []
        address_groups[norm_addr].append(prop)
    
    # Find groups with duplicates
    duplicates = []
    for norm_addr, props in address_groups.items():
        if len(props) > 1:
            # Check if addresses are actually similar (fuzzy matching)
            base_addr = props[0]['address']
            group = [props[0]]
            for prop in props[1:]:
                similarity = difflib.SequenceMatcher(None, base_addr.lower(), prop['address'].lower()).ratio()
                if similarity > 0.85:  # 85% similar
                    group.append(prop)
            
            if len(group) > 1:
                duplicates.append(group)
    
    return duplicates

def choose_primary_property(group):
    """Choose which property to keep as primary"""
    # Priority: most attachments > has MLS > most recent update > oldest creation
    
    # Sort by attachment count (desc), has MLS, updated_at (desc), created_at (asc)
    sorted_group = sorted(group, key=lambda p: (
        -p.get('attachment_count', 0),
        1 if p.get('mls_number') else 0,
        p.get('updated_at') or datetime.min,
        -(p.get('created_at') or datetime.max).timestamp()  # Negative for ascending
    ), reverse=True)
    
    return sorted_group[0]

def merge_properties(cursor, primary, duplicates):
    """Merge duplicate properties into primary, then delete duplicates"""
    primary_id = primary['id']
    duplicate_ids = [d['id'] for d in duplicates]
    
    actions = []
    
    # 1. Move attachments from duplicates to primary
    for dup_id in duplicate_ids:
        cursor.execute("""
            UPDATE attachments 
            SET property_id = %s
            WHERE property_id = %s
        """, (primary_id, dup_id))
        if cursor.rowcount > 0:
            actions.append(f"Moved {cursor.rowcount} attachments from property {dup_id}")
    
    # 2. Move property_emails from duplicates to primary
    for dup_id in duplicate_ids:
        cursor.execute("""
            UPDATE property_emails 
            SET property_id = %s
            WHERE property_id = %s
            ON CONFLICT (gmail_message_id) DO NOTHING
        """, (primary_id, dup_id))
        if cursor.rowcount > 0:
            actions.append(f"Moved {cursor.rowcount} emails from property {dup_id}")
    
    # 3. Move status_history
    for dup_id in duplicate_ids:
        cursor.execute("""
            UPDATE status_history 
            SET property_id = %s
            WHERE property_id = %s
        """, (primary_id, dup_id))
        if cursor.rowcount > 0:
            actions.append(f"Moved {cursor.rowcount} status history records from property {dup_id}")
    
    # 4. Move important_property_info
    for dup_id in duplicate_ids:
        cursor.execute("""
            UPDATE important_property_info 
            SET property_id = %s
            WHERE property_id = %s
        """, (primary_id, dup_id))
        if cursor.rowcount > 0:
            actions.append(f"Moved {cursor.rowcount} important info records from property {dup_id}")
    
    # 5. Move compliance_alerts
    for dup_id in duplicate_ids:
        cursor.execute("""
            UPDATE compliance_alerts 
            SET property_id = %s
            WHERE property_id = %s
        """, (primary_id, dup_id))
        if cursor.rowcount > 0:
            actions.append(f"Moved {cursor.rowcount} compliance alerts from property {dup_id}")
    
    # 6. Move property_flags
    for dup_id in duplicate_ids:
        cursor.execute("""
            UPDATE property_flags 
            SET property_id = %s
            WHERE property_id = %s
        """, (primary_id, dup_id))
        if cursor.rowcount > 0:
            actions.append(f"Moved {cursor.rowcount} flags from property {dup_id}")
    
    # 7. Update primary property's attachment count
    cursor.execute("""
        UPDATE properties SET 
            attachment_count = (SELECT COUNT(*) FROM attachments WHERE property_id = %s),
            has_attachments = (SELECT COUNT(*) > 0 FROM attachments WHERE property_id = %s),
            updated_at = NOW()
        WHERE id = %s
    """, (primary_id, primary_id, primary_id))
    
    # 8. Delete duplicate properties
    for dup_id in duplicate_ids:
        cursor.execute("DELETE FROM properties WHERE id = %s", (dup_id,))
        actions.append(f"Deleted duplicate property {dup_id}")
    
    return actions

def add_unique_constraint(cursor):
    """Add constraint to prevent future duplicates"""
    try:
        # Create a function to normalize addresses for the constraint
        cursor.execute("""
            CREATE OR REPLACE FUNCTION normalize_address(text) RETURNS text AS $$
                SELECT LOWER(REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        REGEXP_REPLACE(
                            REGEXP_REPLACE(
                                REGEXP_REPLACE($1, '\\s+', ' ', 'g'),
                                ' street$| street ', ' st ', 'g'
                            ),
                            ' avenue$| avenue ', ' ave ', 'g'
                        ),
                        ' road$| road ', ' rd ', 'g'
                    ),
                    ' boulevard$| boulevard ', ' blvd ', 'g'
                ))
            $$ LANGUAGE SQL IMMUTABLE;
        """)
        
        # Create unique index on normalized address
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_properties_normalized_address 
            ON properties (normalize_address(address))
            WHERE address IS NOT NULL
        """)
        
        print("✓ Added unique constraint on normalized address")
        return True
    except Exception as e:
        print(f"⚠ Could not add unique constraint (may already exist): {e}")
        return False

def main():
    print("=" * 60)
    print("PROPERTY DEDUPLICATION SCRIPT")
    print("=" * 60)
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Find duplicates
        print("\nFinding duplicate properties...")
        duplicate_groups = find_duplicates(cursor)
        
        if not duplicate_groups:
            print("✓ No duplicates found!")
            return
        
        print(f"\nFound {len(duplicate_groups)} groups of duplicates:")
        for i, group in enumerate(duplicate_groups, 1):
            print(f"\nGroup {i}:")
            for prop in group:
                print(f"  ID {prop['id']}: {prop['address']}")
                print(f"    MLS: {prop.get('mls_number', 'None')}, Attachments: {prop.get('attachment_count', 0)}")
        
        # Ask for confirmation
        print("\n" + "=" * 60)
        response = input("Proceed with deduplication? (yes/no): ")
        if response.lower() != 'yes':
            print("Aborted.")
            return
        
        # Process each group
        total_merged = 0
        for i, group in enumerate(duplicate_groups, 1):
            print(f"\nProcessing group {i}/{len(duplicate_groups)}...")
            primary = choose_primary_property(group)
            duplicates = [p for p in group if p['id'] != primary['id']]
            
            print(f"  Primary: ID {primary['id']} - {primary['address']}")
            print(f"  Merging {len(duplicates)} duplicate(s)...")
            
            actions = merge_properties(cursor, primary, duplicates)
            for action in actions:
                print(f"    {action}")
            
            total_merged += len(duplicates)
        
        # Add constraint to prevent future duplicates
        print("\nAdding constraint to prevent future duplicates...")
        add_unique_constraint(cursor)
        
        # Commit changes
        conn.commit()
        
        print("\n" + "=" * 60)
        print(f"✓ SUCCESS: Merged {total_merged} duplicate properties")
        print("=" * 60)
        
    except Exception as e:
        conn.rollback()
        print(f"\n✗ ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    main()

