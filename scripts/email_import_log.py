"""
Email Import Log - Admin View
Diagnostic view to troubleshoot email processing issues
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from tabulate import tabulate

def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="island_properties",
        user="island_user",
        password="island123!"
    )

def show_recent_emails(hours=24):
    """Show emails processed in the last N hours"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cutoff = datetime.now() - timedelta(hours=hours)
    
    cursor.execute("""
        SELECT 
            epl.id,
            epl.email_subject,
            LEFT(epl.email_from, 30) as from_email,
            epl.email_date,
            epl.status,
            epl.property_id,
            CASE 
                WHEN epl.property_id IS NOT NULL THEN 
                    (SELECT address FROM properties WHERE id = epl.property_id)
                ELSE NULL
            END as property_address,
            epl.actions_taken,
            epl.processing_time_ms,
            epl.processed_at
        FROM email_processing_log epl
        WHERE epl.processed_at >= %s
        ORDER BY epl.processed_at DESC
    """, (cutoff,))
    
    rows = cursor.fetchall()
    
    print(f"\n{'='*120}")
    print(f"EMAIL PROCESSING LOG - Last {hours} hours")
    print(f"{'='*120}\n")
    
    if not rows:
        print(f"No emails processed in the last {hours} hours.")
        return
    
    # Summary statistics
    total = len(rows)
    successful = sum(1 for r in rows if r['status'] == 'success')
    failed = sum(1 for r in rows if r['status'] == 'error')
    no_property = sum(1 for r in rows if r['status'] == 'no_property_data')
    
    print(f"SUMMARY:")
    print(f"  Total emails processed: {total}")
    print(f"  Successful: {successful} ({successful/total*100:.1f}%)")
    print(f"  Failed: {failed}")
    print(f"  No property data: {no_property}")
    
    # Detailed table
    print(f"\nDETAILED LOG:")
    
    table_data = []
    for row in rows:
        actions = row['actions_taken'] or '[]'
        if len(actions) > 40:
            actions = actions[:37] + '...'
        
        subject = row['email_subject']
        if len(subject) > 50:
            subject = subject[:47] + '...'
        
        table_data.append([
            row['id'],
            row['processed_at'].strftime('%m/%d %H:%M'),
            subject,
            row['status'][:15],
            row['property_id'] or '-',
            actions,
            f"{row['processing_time_ms']}ms" if row['processing_time_ms'] else '-'
        ])
    
    print(tabulate(table_data, 
                   headers=['ID', 'Time', 'Subject', 'Status', 'Prop ID', 'Actions', 'Time'],
                   tablefmt='grid'))
    
    cursor.close()
    conn.close()

def show_failed_emails():
    """Show failed email processing attempts"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("""
        SELECT 
            epl.id,
            epl.email_subject,
            epl.email_from,
            epl.error_message,
            epl.processed_at
        FROM email_processing_log epl
        WHERE epl.status = 'error'
        ORDER BY epl.processed_at DESC
        LIMIT 20
    """)
    
    rows = cursor.fetchall()
    
    print(f"\n{'='*120}")
    print(f"FAILED EMAIL PROCESSING ATTEMPTS")
    print(f"{'='*120}\n")
    
    if not rows:
        print("No failed emails found.")
        return
    
    for row in rows:
        print(f"ID: {row['id']}")
        print(f"Time: {row['processed_at']}")
        print(f"Subject: {row['email_subject']}")
        print(f"From: {row['email_from']}")
        print(f"Error: {row['error_message']}")
        print("-" * 80)
    
    cursor.close()
    conn.close()

def show_attachment_stats():
    """Show attachment processing statistics"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # Attachment counts by category
    cursor.execute("""
        SELECT 
            category,
            COUNT(*) as count,
            SUM(CASE WHEN is_foil THEN 1 ELSE 0 END) as foil_count
        FROM attachments
        GROUP BY category
        ORDER BY count DESC
    """)
    
    categories = cursor.fetchall()
    
    # Recent attachments
    cursor.execute("""
        SELECT 
            a.filename,
            a.category,
            a.is_foil,
            a.uploaded_at,
            p.address
        FROM attachments a
        LEFT JOIN properties p ON p.id = a.property_id
        ORDER BY a.uploaded_at DESC
        LIMIT 10
    """)
    
    recent = cursor.fetchall()
    
    print(f"\n{'='*120}")
    print(f"ATTACHMENT STATISTICS")
    print(f"{'='*120}\n")
    
    print("ATTACHMENTS BY CATEGORY:")
    table_data = [[cat['category'], cat['count'], cat['foil_count']] for cat in categories]
    print(tabulate(table_data, headers=['Category', 'Total', 'FOIL'], tablefmt='grid'))
    
    print(f"\nRECENT ATTACHMENTS:")
    table_data = []
    for att in recent:
        filename = att['filename']
        if len(filename) > 40:
            filename = filename[:37] + '...'
        
        address = att['address'] or 'N/A'
        if len(address) > 40:
            address = address[:37] + '...'
        
        table_data.append([
            att['uploaded_at'].strftime('%m/%d %H:%M'),
            filename,
            att['category'],
            '✓' if att['is_foil'] else '',
            address
        ])
    
    print(tabulate(table_data, 
                   headers=['Time', 'Filename', 'Category', 'FOIL', 'Property'],
                   tablefmt='grid'))
    
    cursor.close()
    conn.close()

def show_email_details(email_id):
    """Show detailed information about a specific email"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("""
        SELECT *
        FROM email_processing_log
        WHERE id = %s OR email_subject LIKE %s
    """, (email_id, f'%{email_id}%'))
    
    row = cursor.fetchone()
    
    if not row:
        print(f"No email found with ID or subject containing: {email_id}")
        return
    
    print(f"\n{'='*120}")
    print(f"EMAIL DETAILS - ID {row['id']}")
    print(f"{'='*120}\n")
    
    print(f"Gmail ID: {row['email_id']}")
    print(f"Subject: {row['email_subject']}")
    print(f"From: {row['email_from']}")
    print(f"Date: {row['email_date']}")
    print(f"Status: {row['status']}")
    print(f"Property ID: {row['property_id']}")
    print(f"Actions: {row['actions_taken']}")
    print(f"Processing Time: {row['processing_time_ms']}ms")
    print(f"Processed At: {row['processed_at']}")
    
    if row['error_message']:
        print(f"\nERROR MESSAGE:")
        print(row['error_message'])
    
    # Show property if exists
    if row['property_id']:
        cursor.execute("""
            SELECT address, current_status, current_list_price
            FROM properties
            WHERE id = %s
        """, (row['property_id'],))
        
        prop = cursor.fetchone()
        if prop:
            print(f"\nPROPERTY:")
            print(f"  Address: {prop['address']}")
            print(f"  Status: {prop['current_status']}")
            print(f"  Price: {prop['current_list_price']}")
    
    # Show attachments
    cursor.execute("""
        SELECT filename, category, is_foil, file_size
        FROM attachments
        WHERE source_email_id = %s
    """, (row['email_id'],))
    
    attachments = cursor.fetchall()
    if attachments:
        print(f"\nATTACHMENTS ({len(attachments)}):")
        for att in attachments:
            foil = " [FOIL]" if att['is_foil'] else ""
            print(f"  • {att['filename']} ({att['category']}){foil}")
    
    cursor.close()
    conn.close()

def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python email_import_log.py recent [hours]    - Show recent emails (default 24 hours)")
        print("  python email_import_log.py failed           - Show failed processing attempts")
        print("  python email_import_log.py attachments      - Show attachment statistics")
        print("  python email_import_log.py detail <id>      - Show details for specific email")
        return
    
    command = sys.argv[1].lower()
    
    if command == 'recent':
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        show_recent_emails(hours)
    elif command == 'failed':
        show_failed_emails()
    elif command == 'attachments':
        show_attachment_stats()
    elif command == 'detail':
        if len(sys.argv) < 3:
            print("Error: Provide email ID or subject keyword")
            return
        show_email_details(sys.argv[2])
    else:
        print(f"Unknown command: {command}")

if __name__ == '__main__':
    main()

