#!/usr/bin/env python3
"""
ACT Spreadsheet Parser and Reconciliation - FIXED VERSION
Parses PDF spreadsheet from ACT database and compares with email database
"""

import pdfplumber
import re
import psycopg2
import psycopg2.extras
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        database=os.getenv('DB_NAME', 'island_properties'),
        user=os.getenv('DB_USER', 'island_user'),
        password=os.getenv('DB_PASSWORD'),
        port=os.getenv('DB_PORT', '5432')
    )


def normalize_address(address):
    """Normalize address for comparison"""
    if not address:
        return ""
    
    # Convert to lowercase
    addr = address.lower().strip()
    
    # Remove extra whitespace
    addr = re.sub(r'\s+', ' ', addr)
    
    # Common abbreviations
    replacements = {
        ' street': ' st',
        ' road': ' rd',
        ' avenue': ' ave',
        ' boulevard': ' blvd',
        ' drive': ' dr',
        ' lane': ' ln',
        ' court': ' ct',
        ' place': ' pl',
        ' unit': ' apt',
        'apartment': 'apt',
    }
    
    for old, new in replacements.items():
        addr = addr.replace(old, new)
    
    # Remove periods
    addr = addr.replace('.', '')
    
    return addr


def extract_street_number(address):
    """Extract street number from address for better matching"""
    match = re.match(r'^(\d+[\-/]?\d*)', address.strip())
    return match.group(1) if match else None


def normalize_reo_status(s):
    """Normalize REO status strings to canonical form"""
    if not s:
        return s
    sl = s.lower().strip()
    if sl in ('pending', 'under contract', 'pended', 'in contract'):
        return 'Incontract'
    if sl in ('1/2 signed', '1/2 signed contract', 'half signed',
              '½ signed', '½ signed contract', '1/2 signed contract'):
        return '½ Signed'
    if sl in ('available', 'lpp', 'auction/available', 'auction available'):
        return 'Auction/Available'
    if sl in ('1st accept', '1st accepted', 'first accepted', 'first accept'):
        return '1st Accepted'
    if sl in ('t-o-t-m', 'temporarily off the market', 'totm'):
        return 'TOTM'
    if sl in ('highest and best', 'highest & best', 'h&b', 'h & b'):
        return 'H&B'
    if sl in ('sold',):
        return 'Sold'
    if sl in ('closed',):
        return 'Closed'
    if sl in ('price reduced', 'price reduction', 'reduced'):
        return None
    return s


def parse_act_pdf(pdf_path):
    """
    Parse ACT spreadsheet PDF and extract property data
    FIXED: Correct column mapping for February 2026 format
    """
    properties = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            
            if not tables:
                continue
            
            for table in tables:
                # Skip header row
                for row in table[1:]:
                    if not row or len(row) < 6:  # Flexible: some PDFs have 7-8+ cols
                        continue
                    
                    try:
                        # FIXED COLUMN MAPPING (8 columns including Financing):
                        # Col 0: REO Status
                        # Col 1: Financing
                        # Col 2: Prop Style
                        # Col 3: Address 1
                        # Col 4: Address 2
                        # Col 5: City
                        # Col 6: List Date
                        # Col 7: List Price
                        
                        reo_status = row[0] if row[0] else ""
                        financing  = row[1] if len(row) > 1 and row[1] else ""
                        prop_style = row[2] if len(row) > 2 and row[2] else ""
                        address1   = row[3] if len(row) > 3 and row[3] else ""
                        address2   = row[4] if len(row) > 4 and row[4] else ""
                        city       = row[5] if len(row) > 5 and row[5] else ""
                        list_date  = row[6] if len(row) > 6 and row[6] else ""
                        list_price = row[7] if len(row) > 7 and row[7] else ""
                        
                        # Skip empty rows
                        if not address1 or not city:
                            continue
                        
                        # Build full address
                        full_address = f"{address1}"
                        if address2 and address2.strip() and address2.strip() != '.':
                            full_address += f" {address2}"
                        full_address += f", {city}"
                        
                        # Clean list price
                        price_clean = None
                        if list_price:
                            price_match = re.search(r'\$\s*([\d,]+)', list_price)
                            if price_match:
                                price_clean = float(price_match.group(1).replace(',', ''))
                        
                        property_data = {
                            'reo_status': reo_status.strip() if reo_status else None,
                            'reo_status_normalized': normalize_reo_status(reo_status.strip()) if reo_status else None,
                            'manager': None,  # Not in this PDF format
                            'financing': None,  # Not in this PDF format
                            'prop_style': prop_style.strip() if prop_style else None,
                            'address': full_address.strip(),
                            'address_normalized': normalize_address(full_address),
                            'street_number': extract_street_number(address1),
                            'city': city.strip() if city else None,
                            'list_date': list_date.strip() if list_date else None,
                            'list_price': price_clean
                        }
                        
                        properties.append(property_data)
                        
                    except Exception as e:
                        print(f"Error parsing row: {e}")
                        continue
    
    return properties


def get_database_properties():
    """Get all properties from database"""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cur.execute("""
        SELECT 
            id,
            address,
            current_list_price,
            current_status,
            created_at,
            data_source
        FROM properties
        ORDER BY id
    """)
    
    properties = cur.fetchall()
    
    # Normalize addresses for comparison
    for prop in properties:
        prop['address_normalized'] = normalize_address(prop['address'])
        prop['street_number'] = extract_street_number(prop['address'])
    
    cur.close()
    conn.close()
    
    return properties


def find_matching_property(act_prop, db_properties):
    """
    Try to find matching property in database
    Returns (match, confidence_score)
    """
    best_match = None
    best_score = 0
    
    act_normalized = act_prop['address_normalized']
    act_street_num = act_prop['street_number']
    
    for db_prop in db_properties:
        score = 0
        
        # Street number must match (critical)
        if act_street_num and db_prop['street_number']:
            if act_street_num != db_prop['street_number']:
                continue
            score += 3
        
        db_normalized = db_prop['address_normalized']
        
        # Exact match
        if act_normalized == db_normalized:
            return db_prop, 10
        
        # Partial match
        if act_normalized in db_normalized or db_normalized in act_normalized:
            score += 5
        
        # Word overlap
        act_words = set(act_normalized.split())
        db_words = set(db_normalized.split())
        common_words = act_words & db_words
        
        if len(common_words) > 0:
            overlap_ratio = len(common_words) / max(len(act_words), len(db_words))
            score += overlap_ratio * 3
        
        if score > best_score:
            best_score = score
            best_match = db_prop
    
    # Require minimum confidence
    if best_score >= 5:
        return best_match, best_score
    
    return None, 0


def reconcile_act_vs_database(pdf_path):
    """Compare ACT spreadsheet against database"""
    print("=" * 80)
    print("ACT SPREADSHEET RECONCILIATION")
    print("=" * 80)
    
    print("\n1. Parsing ACT spreadsheet PDF...")
    act_properties = parse_act_pdf(pdf_path)
    print(f"   Found {len(act_properties)} properties in ACT spreadsheet")
    
    print("\n2. Loading database properties...")
    db_properties = get_database_properties()
    print(f"   Found {len(db_properties)} properties in database")
    
    print("\n3. Comparing ACT vs Database...")
    
    results = {
        'matched': [],
        'in_act_not_db': [],
        'in_db_not_act': [],
        'timestamp': datetime.now().isoformat()
    }
    
    matched_db_ids = set()
    
    for act_prop in act_properties:
        match, confidence = find_matching_property(act_prop, db_properties)
        
        if match:
            results['matched'].append({
                'act_address': act_prop['address'],
                'db_address': match['address'],
                'db_id': match['id'],
                'confidence': confidence,
                'act_price': act_prop['list_price'],
                'db_price': float(match['current_list_price']) if match['current_list_price'] else None,
                'reo_status': act_prop['reo_status'],
                'reo_status_normalized': act_prop.get('reo_status_normalized'),
                'db_status': match['current_status'],
                'manager': act_prop['manager'],
                'financing': act_prop.get('financing'),
                'prop_style': act_prop.get('prop_style')
            })
            matched_db_ids.add(match['id'])
        else:
            results['in_act_not_db'].append({
                'address': act_prop['address'],
                'reo_status': act_prop['reo_status'],
                'reo_status_normalized': act_prop.get('reo_status_normalized'),
                'manager': act_prop['manager'] or 'N/A',
                'financing': act_prop.get('financing'),
                'prop_style': act_prop.get('prop_style'),
                'city': act_prop.get('city'),
                'list_price': act_prop['list_price'],
                'list_date': act_prop['list_date'],
                'reason': 'Agent did not send email for this property'
            })
    
    for db_prop in db_properties:
        if db_prop['id'] not in matched_db_ids:
            results['in_db_not_act'].append({
                'db_id': db_prop['id'],
                'address': db_prop['address'],
                'db_status': db_prop['current_status'],
                'created_at': db_prop['created_at'].isoformat() if db_prop['created_at'] else None,
                'data_source': db_prop['data_source'],
                'reason': 'Property exists in database but not in ACT'
            })
    
    print("\n" + "=" * 80)
    print("RECONCILIATION SUMMARY")
    print("=" * 80)
    print(f"\n✓ Matched Properties: {len(results['matched'])}")
    print(f"⚠️  In ACT but NOT in Database: {len(results['in_act_not_db'])}")
    print(f"ℹ️  In Database but NOT in ACT: {len(results['in_db_not_act'])}")
    
    return results


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python act_reconciliation_fixed.py <path_to_act_pdf>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)
    
    results = reconcile_act_vs_database(pdf_path)
    
    if results['in_act_not_db']:
        print("\n" + "=" * 80)
        print("⚠️  PROPERTIES IN ACT BUT NOT IN DATABASE:")
        print("=" * 80)
        for prop in results['in_act_not_db'][:20]:
            print(f"\n• {prop['address']}")
            print(f"  Status: {prop['reo_status']}")
            print(f"  List Price: ${prop['list_price']:,.0f}" if prop['list_price'] else "  List Price: N/A")
