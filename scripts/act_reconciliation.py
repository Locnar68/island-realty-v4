#!/usr/bin/env python3
"""
ACT Spreadsheet Parser and Reconciliation
Parses PDF spreadsheet from ACT database and compares with email database.

HEADER-BASED COLUMN MAPPING: All column lookups use header names, never
positional indices. Rob may reorder columns freely without breaking this parser.
"""

import pdfplumber
import re
import psycopg2
import psycopg2.extras
from datetime import datetime, date
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

    addr = address.lower().strip()
    addr = re.sub(r'\s+', ' ', addr)

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
              '\u00bd signed', '\u00bd signed contract', '1/2 signed contract'):
        return '\u00bd Signed'
    if sl in ('available', 'lpp'):
        return 'Available'
    if sl in ('auction/available', 'auction available'):
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


# ---------------------------------------------------------------------------
# Header-name → column-index resolver
# Used for both PDF table rows and any future tabular source.
# ---------------------------------------------------------------------------

def build_col_map(header_row):
    """
    Build a case-insensitive {canonical_name: index} map from a header row.
    Strips whitespace and newlines that pdfplumber sometimes embeds in cells.
    """
    col_map = {}
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        key = str(cell).strip().lower().replace('\n', ' ')
        col_map[key] = i
    return col_map


def find_col_index(col_map, candidates):
    """
    Return the column index for the first matching candidate header name.
    candidates: list of lowercase strings to try (partial substring match).
    Returns None if not found.
    """
    for cand in candidates:
        # Exact match first
        if cand in col_map:
            return col_map[cand]
        # Substring match fallback
        for key, idx in col_map.items():
            if cand in key or key in cand:
                return idx
    return None


def cell_val(row, idx, default=''):
    """Safe cell accessor — returns default if index out of range or cell is None."""
    if idx is None or idx >= len(row) or row[idx] is None:
        return default
    return str(row[idx]).strip()


def parse_listing_date(raw):
    """
    Parse a listing date from various formats that may appear in the ACT PDF:
      - 'MM/DD/YYYY'
      - 'M/D/YY'
      - 'YYYY-MM-DD'
      - Excel serial number as string (e.g. '45678')
    Returns a datetime.date or None.
    """
    if not raw or str(raw).strip().lower() in ('', 'none', 'nan'):
        return None
    raw = str(raw).strip()

    # Standard date formats
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%d-%b-%Y', '%B %d, %Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass

    # Excel serial number (days since 1900-01-00)
    try:
        serial = int(float(raw))
        if 30000 < serial < 60000:   # sanity range: ~1982–2064
            from datetime import timedelta
            base = date(1899, 12, 30)
            return base + timedelta(days=serial)
    except (ValueError, OverflowError):
        pass

    return None


# ---------------------------------------------------------------------------
# PDF parser — header-based
# ---------------------------------------------------------------------------

def parse_act_pdf(pdf_path):
    """
    Parse ACT spreadsheet PDF and extract property data.

    Column mapping is driven entirely by the header row found in each table.
    Rob can reorder columns freely; only the header names matter.

    Expected headers (case-insensitive, flexible matching):
      REO Status | Financing | Prop Style | Address (1) | Address 2 | City |
      Listing Date | List Price | Occupancy | Agent Access
    """
    properties = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()

            if not tables:
                continue

            for table in tables:
                if not table or len(table) < 2:
                    continue

                # ── Identify header row ──────────────────────────────────────
                # Use the first row that contains recognisable column names.
                header_row_idx = None
                col_map = {}
                for ri, row in enumerate(table[:3]):   # look in first 3 rows
                    if not row:
                        continue
                    candidate = build_col_map(row)
                    # Must contain at least an address-like and status-like header
                    has_addr = any(
                        k for k in candidate
                        if 'address' in k or 'street' in k or 'addr' in k
                    )
                    has_status = any(
                        k for k in candidate
                        if 'status' in k or 'reo' in k
                    )
                    if has_addr or has_status:
                        header_row_idx = ri
                        col_map = candidate
                        break

                if header_row_idx is None:
                    # No recognisable header — fall back to row 0 silently
                    header_row_idx = 0
                    col_map = build_col_map(table[0])

                # ── Resolve named column indices ─────────────────────────────
                idx_status      = find_col_index(col_map, ['reo status', 'status'])
                idx_financing   = find_col_index(col_map, ['financing', 'finance'])
                idx_style       = find_col_index(col_map, ['prop style', 'style', 'type'])
                idx_addr1       = find_col_index(col_map, ['address 1', 'address1', 'address', 'street'])
                idx_addr2       = find_col_index(col_map, ['address 2', 'address2', 'unit', 'suite'])
                idx_city        = find_col_index(col_map, ['city', 'town'])
                idx_listing_date = find_col_index(col_map, ['listing date', 'list date', 'date listed', 'listed date'])
                idx_price       = find_col_index(col_map, ['list price', 'listing price', 'price', 'asking'])
                idx_occupancy   = find_col_index(col_map, ['occupancy', 'occupied'])
                idx_agent_access = find_col_index(col_map, ['agent access', 'access'])

                # ── Parse data rows ──────────────────────────────────────────
                for row in table[header_row_idx + 1:]:
                    if not row:
                        continue

                    # Need at minimum 3 non-None cells to be a real data row
                    non_empty = sum(1 for c in row if c and str(c).strip())
                    if non_empty < 3:
                        continue

                    try:
                        reo_status   = cell_val(row, idx_status)
                        financing    = cell_val(row, idx_financing)
                        prop_style   = cell_val(row, idx_style)
                        address1     = cell_val(row, idx_addr1)
                        address2     = cell_val(row, idx_addr2)
                        city         = cell_val(row, idx_city)
                        listing_date_raw = cell_val(row, idx_listing_date)
                        list_price_s = cell_val(row, idx_price)
                        occupancy    = cell_val(row, idx_occupancy)
                        agent_access = cell_val(row, idx_agent_access)

                        # Skip rows without an address
                        if not address1 or not city:
                            continue

                        # Build full address
                        full_address = address1
                        if address2 and address2 not in ('.', ''):
                            full_address += f' {address2}'
                        full_address += f', {city}'

                        # Parse listing date
                        listing_date = parse_listing_date(listing_date_raw)

                        # Clean list price
                        price_clean = None
                        if list_price_s:
                            price_match = re.search(r'\$\s*[\d,]+', list_price_s)
                            if price_match:
                                price_clean = float(
                                    price_match.group(0).replace('$', '').replace(',', '').strip()
                                )

                        property_data = {
                            'reo_status': reo_status or None,
                            'reo_status_normalized': normalize_reo_status(reo_status) if reo_status else None,
                            'manager': None,
                            'financing': financing or None,
                            'occupancy': occupancy or None,
                            'agent_access': agent_access or None,
                            'hold_harmless': bool(agent_access and 'hold harmless' in agent_access.lower()),
                            'prop_style': prop_style or None,
                            'address': full_address.strip(),
                            'address_normalized': normalize_address(full_address),
                            'street_number': extract_street_number(address1),
                            'city': city or None,
                            'list_price': price_clean,
                            'listing_date': listing_date,      # datetime.date or None
                        }

                        properties.append(property_data)

                    except Exception as e:
                        print(f'Error parsing row on page {page_num}: {e}')
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
            listing_date,
            last_activity_date,
            data_source
        FROM properties
        ORDER BY id
    """)

    properties = cur.fetchall()

    for prop in properties:
        prop['address_normalized'] = normalize_address(prop['address'])
        prop['street_number'] = extract_street_number(prop['address'])

    cur.close()
    conn.close()

    return properties


def find_matching_property(act_prop, db_properties):
    """
    Try to find matching property in database.
    Returns (match, confidence_score)
    """
    best_match = None
    best_score = 0

    act_normalized = act_prop['address_normalized']
    act_street_num = act_prop['street_number']

    for db_prop in db_properties:
        score = 0

        if act_street_num and db_prop['street_number']:
            if act_street_num != db_prop['street_number']:
                continue
            score += 3

        db_normalized = db_prop['address_normalized']

        if act_normalized == db_normalized:
            return db_prop, 10

        if act_normalized in db_normalized or db_normalized in act_normalized:
            score += 5

        act_words = set(act_normalized.split())
        db_words = set(db_normalized.split())
        common_words = act_words & db_words

        if common_words:
            overlap_ratio = len(common_words) / max(len(act_words), len(db_words))
            score += overlap_ratio * 3

        if score > best_score:
            best_score = score
            best_match = db_prop

    if best_score >= 5:
        return best_match, best_score

    return None, 0


def reconcile_act_vs_database(pdf_path):
    """Compare ACT spreadsheet against database"""
    print('=' * 80)
    print('ACT SPREADSHEET RECONCILIATION')
    print('=' * 80)

    print('\n1. Parsing ACT spreadsheet PDF...')
    act_properties = parse_act_pdf(pdf_path)
    print(f'   Found {len(act_properties)} properties in ACT spreadsheet')

    has_dates = sum(1 for p in act_properties if p.get('listing_date'))
    print(f'   Properties with Listing Date: {has_dates}')

    print('\n2. Loading database properties...')
    db_properties = get_database_properties()
    print(f'   Found {len(db_properties)} properties in database')

    print('\n3. Comparing ACT vs Database...')

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
                'prop_style': act_prop.get('prop_style'),
                'agent_access': act_prop.get('agent_access'),
                'occupancy': act_prop.get('occupancy'),
                'hold_harmless': act_prop.get('hold_harmless', False),
                'listing_date': act_prop.get('listing_date'),
            })
            matched_db_ids.add(match['id'])
        else:
            results['in_act_not_db'].append({
                'address': act_prop['address'],
                'reo_status': act_prop['reo_status'],
                'reo_status_normalized': act_prop.get('reo_status_normalized'),
                'financing': act_prop.get('financing'),
                'prop_style': act_prop.get('prop_style'),
                'city': act_prop.get('city'),
                'list_price': act_prop['list_price'],
                'agent_access': act_prop.get('agent_access'),
                'occupancy': act_prop.get('occupancy'),
                'hold_harmless': act_prop.get('hold_harmless', False),
                'listing_date': act_prop.get('listing_date'),
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

    print('\n' + '=' * 80)
    print('RECONCILIATION SUMMARY')
    print('=' * 80)
    print(f'\n\u2713 Matched Properties: {len(results["matched"])}')
    print(f'\u26a0\ufe0f  In ACT but NOT in Database: {len(results["in_act_not_db"])}')
    print(f'\u2139\ufe0f  In Database but NOT in ACT: {len(results["in_db_not_act"])}')

    return results


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print('Usage: python act_reconciliation.py <path_to_act_pdf>')
        sys.exit(1)

    pdf_path = sys.argv[1]

    if not os.path.exists(pdf_path):
        print(f'Error: File not found: {pdf_path}')
        sys.exit(1)

    results = reconcile_act_vs_database(pdf_path)

    if results['in_act_not_db']:
        print('\n' + '=' * 80)
        print('\u26a0\ufe0f  PROPERTIES IN ACT BUT NOT IN DATABASE:')
        print('=' * 80)
        for prop in results['in_act_not_db'][:20]:
            print(f'\n\u2022 {prop["address"]}')
            print(f'  Status: {prop["reo_status"]}')
            if prop['list_price']:
                print(f'  List Price: ${prop["list_price"]:,.0f}')
            else:
                print('  List Price: N/A')
            if prop.get('listing_date'):
                print(f'  Listing Date: {prop["listing_date"]}')
