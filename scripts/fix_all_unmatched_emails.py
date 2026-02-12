#!/usr/bin/env python3
"""
Automated Fix for All 12 Unmatched Important Emails
Creates missing properties and links all unmatched emails
"""

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
import os
import sys
import json
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


def create_property(conn, address, status='Available'):
    """Create a new property with basic info"""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Check if property already exists
    cur.execute("""
        SELECT id FROM properties 
        WHERE address ILIKE %s
    """, (f'%{address}%',))
    existing = cur.fetchone()
    
    if existing:
        print(f"      Property already exists: ID {existing['id']}")
        return existing['id']
    
    # Create new property
    cur.execute("""
        INSERT INTO properties
        (address, current_status, data_source, created_at, updated_at)
        VALUES (%s, %s, %s, NOW(), NOW())
        RETURNING id
    """, (address, status, 'email'))
    
    new_id = cur.fetchone()['id']
    conn.commit()
    
    print(f"      ✓ Created property ID {new_id}: {address}")
    return new_id


def link_email_to_property(conn, email_id, property_id, email_subject, status_change=None):
    """Link an email to a property and optionally update status"""
    cur = conn.cursor()
    
    # Update email_processing_log
    cur.execute("""
        UPDATE email_processing_log
        SET property_id = %s,
            actions_taken = %s::jsonb
        WHERE email_id = %s
    """, (property_id, json.dumps(['property_matched', 'automated_fix']), email_id))
    
    rows_updated = cur.rowcount
    
    # Update status if provided
    if status_change:
        cur.execute("""
            UPDATE properties
            SET current_status = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (status_change, property_id))
        
        # Add status history
        cur.execute("""
            INSERT INTO status_history
            (property_id, new_status, source_email_id, source_email_subject, changed_by)
            VALUES (%s, %s, %s, %s, %s)
        """, (property_id, status_change, email_id, email_subject, 'automated_fix'))
    
    conn.commit()
    
    if rows_updated > 0:
        print(f"      ✓ Linked email to property {property_id}")
    else:
        print(f"      ⚠️  Email may already be linked")


def add_important_info(conn, property_id, category, title, content, severity='warning', email_id=None):
    """Add important property information"""
    cur = conn.cursor()
    
    cur.execute("""
        INSERT INTO important_property_info
        (property_id, category, title, content, severity, source_email_id)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (property_id, category, title, content, severity, email_id))
    
    conn.commit()


def main():
    print("=" * 80)
    print("AUTOMATED FIX: 12 UNMATCHED IMPORTANT EMAILS")
    print("=" * 80)
    
    conn = get_connection()
    
    fixes = []
    
    # ==========================================================================
    # FIX #1-2: Highest & Best emails with existing properties
    # ==========================================================================
    print("\n1. FIXING: Highest & Best - 24-11 37th Avenue (Link to ID 96)")
    print("-" * 80)
    try:
        link_email_to_property(
            conn,
            '19c444317de96033',
            96,
            'Highest & Best Notification: 24-11 37th Avenue Long Island City NY 11101',
            status_change='First Accepted'
        )
        add_important_info(
            conn, 96, 'Deadlines', 'Multiple Offer Situation',
            'Highest & Best offers requested. Check email for deadline.',
            'critical', '19c444317de96033'
        )
        fixes.append("✓ 24-11 37th Avenue linked to property 96")
    except Exception as e:
        print(f"      ✗ Error: {e}")
        fixes.append(f"✗ 24-11 37th Avenue failed: {e}")
    
    print("\n2. FIXING: Highest & Best - 283 West Neck Road (Link to ID 91)")
    print("-" * 80)
    try:
        link_email_to_property(
            conn,
            '19c43b1d615643f1',
            91,
            'Highest & Best Notification: 283 West Neck Road Huntington NY 11743',
            status_change='First Accepted'
        )
        add_important_info(
            conn, 91, 'Deadlines', 'Multiple Offer Situation',
            'Highest & Best offers requested. Check email for deadline.',
            'critical', '19c43b1d615643f1'
        )
        fixes.append("✓ 283 West Neck Road linked to property 91")
    except Exception as e:
        print(f"      ✗ Error: {e}")
        fixes.append(f"✗ 283 West Neck Road failed: {e}")
    
    # ==========================================================================
    # FIX #3: CRITICAL - 2217 Collier Avenue (Create property + 3 emails)
    # ==========================================================================
    print("\n3. FIXING: 2217 Collier Avenue Far Rockaway (Create + Link 3 emails)")
    print("-" * 80)
    try:
        property_id = create_property(
            conn,
            '2217 Collier Avenue, Far Rockaway, NY 11691',
            status='First Accepted'  # Has Highest & Best
        )
        
        # Link Highest & Best email
        print("   Linking Highest & Best email...")
        link_email_to_property(
            conn,
            '19c33d42ef0eb499',
            property_id,
            'Highest & Best Notification: 2217 Collier Avenue Far Rockaway NY 11691'
        )
        
        # Link Price Reduction email
        print("   Linking Price Reduction email...")
        link_email_to_property(
            conn,
            '19c29337ea4036cf',
            property_id,
            'Price reduction: 2217 Collier Avenue Far Rockaway NY 11691',
            status_change='Price Reduced'
        )
        
        # Link FOIL email
        print("   Linking FOIL email...")
        link_email_to_property(
            conn,
            '19c33855f1bc90a7',
            property_id,
            'Fw: Foil 2217 Collier'
        )
        
        # Add important info
        add_important_info(
            conn, property_id, 'Deadlines', 'Highest & Best Offers',
            'Multiple offer situation. Highest & Best offers requested.',
            'critical', '19c33d42ef0eb499'
        )
        
        add_important_info(
            conn, property_id, 'FOIL', 'FOIL Documents Available',
            'FOIL documents received. Check attachments.',
            'warning', '19c33855f1bc90a7'
        )
        
        print("      ✓ Created property and linked 3 emails")
        fixes.append(f"✓ 2217 Collier Avenue created (ID {property_id}) + 3 emails")
        
    except Exception as e:
        print(f"      ✗ Error: {e}")
        fixes.append(f"✗ 2217 Collier Avenue failed: {e}")
    
    # ==========================================================================
    # FIX #4: 299 South River Road (Create or link)
    # ==========================================================================
    print("\n4. FIXING: 299 South River Road Calverton (Create if needed)")
    print("-" * 80)
    try:
        # Check if this exists
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, address FROM properties 
            WHERE address ILIKE '%299%south%river%calverton%'
        """)
        existing = cur.fetchone()
        
        if existing:
            property_id = existing['id']
            print(f"      Property exists: ID {property_id}")
        else:
            property_id = create_property(
                conn,
                '299 South River Road, Calverton, NY 11933',
                status='First Accepted'
            )
        
        # Link the status update email
        link_email_to_property(
            conn,
            '19c4450c5f5f8671',
            property_id,
            'Status Update: 299 South River Road Calverton NY 11933'
        )
        
        fixes.append(f"✓ 299 South River Road linked to property {property_id}")
        
    except Exception as e:
        print(f"      ✗ Error: {e}")
        fixes.append(f"✗ 299 South River Road failed: {e}")
    
    # ==========================================================================
    # FIX #5-10: Price Reduction emails
    # ==========================================================================
    price_reductions = [
        {
            'name': '825 Morrison 12F',
            'property_id': 70,
            'email_id': '19c43c5acf38c269',
            'subject': 'Price Reduction: 825 Morrison Avenue Unit 12F Bronx NY 10473'
        },
        {
            'name': '825 Morrison 16M',
            'property_id': 68,
            'email_id': '19c437f58dcd2c4f',
            'subject': 'Price Reduction: 825 Morrison Avenue Unit 16M Bronx NY 10473'
        },
        {
            'name': '5730 Mosholu 6A',
            'property_id': 23,
            'email_id': '19c432230af57973',
            'subject': 'Price Reduction: 5730 Mosholu Avenue Unit 6A Bronx NY 10471'
        },
        {
            'name': '536 West 163rd 3D',
            'property_id': 49,
            'email_id': '19c430295b23b311',
            'subject': 'Price Reduction: 536 West 163rd Street Unit 3D New York NY 10032'
        },
        {
            'name': '221 Beach 80th 3D',
            'property_id': 99,
            'email_id': '19c341e3f7e98510',
            'subject': 'Price Reduction: 221 Beach 80th Street Unit 3D Rockaway Beach NY 11693'
        },
        {
            'name': '156 Beach 60th',
            'property_id': 102,
            'email_id': '19c28f3d3b82e250',
            'subject': 'Price Reduction: 156 Beach 60th Street Arverne NY 11692'
        }
    ]
    
    for i, pr in enumerate(price_reductions, start=5):
        print(f"\n{i}. FIXING: Price Reduction - {pr['name']}")
        print("-" * 80)
        try:
            link_email_to_property(
                conn,
                pr['email_id'],
                pr['property_id'],
                pr['subject'],
                status_change='Price Reduced'
            )
            
            # Add price reduction note
            add_important_info(
                conn, pr['property_id'], 'Pricing', 'Price Reduction',
                'Property price has been reduced. See email for details.',
                'warning', pr['email_id']
            )
            
            fixes.append(f"✓ {pr['name']} linked to property {pr['property_id']}")
            
        except Exception as e:
            print(f"      ✗ Error: {e}")
            fixes.append(f"✗ {pr['name']} failed: {e}")
    
    # ==========================================================================
    # SUMMARY
    # ==========================================================================
    print("\n" + "=" * 80)
    print("FIX SUMMARY")
    print("=" * 80)
    
    successful = [f for f in fixes if f.startswith('✓')]
    failed = [f for f in fixes if f.startswith('✗')]
    
    print(f"\nTotal fixes attempted: {len(fixes)}")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")
    
    print("\nResults:")
    for fix in fixes:
        print(f"  {fix}")
    
    # Verification
    print("\n" + "=" * 80)
    print("VERIFICATION")
    print("=" * 80)
    
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Count remaining unmatched important emails
    cur.execute("""
        SELECT COUNT(*) as count
        FROM email_processing_log
        WHERE property_id IS NULL
        AND processing_status = 'success'
        AND email_date >= '2026-02-01'
        AND (
            email_subject ILIKE '%highest%best%'
            OR email_subject ILIKE '%price reduction%'
            OR email_subject ILIKE '%status update%'
        )
    """)
    remaining = cur.fetchone()['count']
    
    print(f"\nRemaining unmatched important emails: {remaining}")
    
    if remaining == 0:
        print("✓ ALL IMPORTANT EMAILS NOW MATCHED!")
    else:
        print(f"⚠️  Still have {remaining} unmatched emails - may need manual review")
    
    # Count properties created
    cur.execute("""
        SELECT COUNT(*) as count
        FROM properties
        WHERE data_source = 'email'
        AND created_at >= NOW() - INTERVAL '1 hour'
    """)
    new_props = cur.fetchone()['count']
    
    print(f"New properties created in last hour: {new_props}")
    
    # Count status updates
    cur.execute("""
        SELECT COUNT(*) as count
        FROM status_history
        WHERE changed_by = 'automated_fix'
        AND updated_at >= NOW() - INTERVAL '1 hour'
    """)
    status_updates = cur.fetchone()['count']
    
    print(f"Status updates applied: {status_updates}")
    
    # Show newly created properties
    cur.execute("""
        SELECT id, address, current_status
        FROM properties
        WHERE created_at >= NOW() - INTERVAL '1 hour'
        ORDER BY id
    """)
    new_properties = cur.fetchall()
    
    if new_properties:
        print(f"\nNewly created properties:")
        for prop in new_properties:
            print(f"  ID {prop['id']}: {prop['address']} (Status: {prop['current_status']})")
    
    conn.close()
    
    print("\n" + "=" * 80)
    print("FIX COMPLETE")
    print("=" * 80)
    print("\nNext steps:")
    print("1. Verify properties appear on website")
    print("2. Retrieve attachments for new properties (especially 2217 Collier FOIL)")
    print("3. Update email_processor.py to recognize these email types automatically")
    print("=" * 80)


if __name__ == '__main__':
    main()

