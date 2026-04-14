#!/usr/bin/env python3
"""
ACT Spreadsheet Parser and Reconciliation — v3 (April 14 2026)

The ACT PDF has one 10-column table per page but a HEADER ROW ONLY ON PAGE 1.
Pages 2-5 are continuation tables with identical column layout but no header.

v1 silently fell back to row 0 as a header on continuation pages, which
produced the April 14 garbage rows ("Unit 6A Unit 6A, Unit 6A").
v2 over-corrected by skipping any table without a header, which threw
away 138 of 189 properties on a real PDF.

v3 does the right thing: **remember the last good header's col_map and
reuse it for subsequent tables in the same PDF.** All 189 properties in
04-12-26_Inventory.pdf now parse correctly (verified locally).

Also includes:
  - Sanity gates from v2 (merged-cell, street-number, addr==city)
  - Address cleanup: strips \\n from wrapped cells, removes
    "User's profile" artifact that pdfplumber injects from PDF metadata
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


# ---------------------------------------------------------------------------
# Address cleanup (NEW in v3)
# ---------------------------------------------------------------------------

_USER_PROFILE_RE = re.compile(r"User'?s profile\w*")
_WS_RE           = re.compile(r'\s+')


def _clean_cell(s):
    """Normalize a cell from pdfplumber: strip \\n wraps and known artifacts."""
    if not s:
        return s
    s = _USER_PROFILE_RE.sub('', s)
    s = _WS_RE.sub(' ', s).strip()
    return s


def normalize_address(address):
    if not address:
        return ""
    addr = address.lower().strip()
    addr = _WS_RE.sub(' ', addr)
    replacements = {
        ' street': ' st', ' road': ' rd', ' avenue': ' ave',
        ' boulevard': ' blvd', ' drive': ' dr', ' lane': ' ln',
        ' court': ' ct', ' place': ' pl', ' unit': ' apt',
        'apartment': 'apt',
    }
    for old, new in replacements.items():
        addr = addr.replace(old, new)
    addr = addr.replace('.', '')
    return addr


def extract_street_number(address):
    match = re.match(r'^(\d+[\-/]?\d*)', address.strip())
    return match.group(1) if match else None


def normalize_reo_status(s):
    if not s:
        return s
    sl = s.lower().strip()
    if sl in ('pending', 'under contract', 'pended', 'in contract'):
        return 'Incontract'
    if sl in ('1/2 signed', '1/2 signed contract', 'half signed',
              '\u00bd signed', '\u00bd signed contract'):
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
    if sl in ('sold',):      return 'Sold'
    if sl in ('closed',):    return 'Closed'
    if sl in ('price reduced', 'price reduction', 'reduced'):
        return None
    return s


# ---------------------------------------------------------------------------
# Column resolver (tightened in v2)
# ---------------------------------------------------------------------------

def build_col_map(header_row):
    col_map = {}
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        key = str(cell).strip().lower().replace('\n', ' ')
        if key and key not in col_map:
            col_map[key] = i
    return col_map


def find_col_index(col_map, candidates):
    for cand in candidates:
        if cand in col_map:
            return col_map[cand]
    for cand in candidates:
        if len(cand) < 4:
            continue
        for key, idx in col_map.items():
            if key.startswith(cand) or key.endswith(cand) \
               or cand.startswith(key) or cand.endswith(key):
                return idx
    return None


def cell_val(row, idx, default=''):
    if idx is None or idx >= len(row) or row[idx] is None:
        return default
    return _clean_cell(str(row[idx]))


def parse_listing_date(raw):
    if not raw or str(raw).strip().lower() in ('', 'none', 'nan'):
        return None
    raw = str(raw).strip()
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%d-%b-%Y', '%B %d, %Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    try:
        serial = int(float(raw))
        if 30000 < serial < 60000:
            from datetime import timedelta
            base = date(1899, 12, 30)
            return base + timedelta(days=serial)
    except (ValueError, OverflowError):
        pass
    return None


# ---------------------------------------------------------------------------
# Sanity gates
# ---------------------------------------------------------------------------

_DIGIT_RUN = re.compile(r'\d')


def _looks_like_merged_cell_row(values):
    non_empty = [v.strip() for v in values if v and v.strip()]
    if len(non_empty) < 3:
        return False
    return len(set(non_empty)) == 1


def _has_street_number(addr):
    if not addr:
        return False
    head = ' '.join(addr.split()[:3])
    return bool(_DIGIT_RUN.search(head))


# ---------------------------------------------------------------------------
# PDF parser — header-based, with page-to-page header carry-over (NEW in v3)
# ---------------------------------------------------------------------------

def parse_act_pdf(pdf_path):
    properties = []
    skipped = {'no_header_no_prev': 0, 'merged_cell': 0, 'no_street_num': 0,
               'addr_eq_city': 0, 'empty': 0, 'no_addr_or_city': 0}

    # State carried across pages: the col_map from the most recent header row.
    # Continuation tables on pages 2-N reuse this.
    last_col_map = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables() or []
            for t_idx, table in enumerate(tables):
                if not table:
                    continue

                # ── Try to find a header row in first 3 rows ─────────────────
                header_row_idx = None
                col_map = {}
                for ri, row in enumerate(table[:3]):
                    if not row:
                        continue
                    cand = build_col_map(row)
                    has_addr = any(
                        k for k in cand
                        if 'address' in k or 'street' in k or 'addr' in k
                    )
                    has_status = any(
                        k for k in cand
                        if 'status' in k or 'reo' in k
                    )
                    if has_addr and has_status:
                        header_row_idx = ri
                        col_map = cand
                        last_col_map = cand  # remember for future tables
                        break

                # ── NEW in v3: continuation-table support ────────────────────
                # No header in this table — inherit the previous page's col_map.
                # All data rows are treated as real data.
                if header_row_idx is None:
                    if last_col_map is None:
                        skipped['no_header_no_prev'] += 1
                        print(f'[skip] page {page_num} table {t_idx}: '
                              f'no header found and no previous header to inherit')
                        continue
                    col_map = last_col_map
                    data_rows = table                     # no header row to skip
                    print(f'[info] page {page_num} table {t_idx}: '
                          f'using header from previous page')
                else:
                    data_rows = table[header_row_idx + 1:]

                # ── Resolve column indices from col_map ─────────────────────
                idx_status       = find_col_index(col_map, ['reo status', 'status'])
                idx_financing    = find_col_index(col_map, ['financing', 'finance'])
                idx_style        = find_col_index(col_map, ['prop style', 'property style', 'style'])
                idx_addr1        = find_col_index(col_map, ['address 1', 'address1', 'address', 'street'])
                idx_addr2        = find_col_index(col_map, ['address 2', 'address2', 'unit', 'suite'])
                idx_city         = find_col_index(col_map, ['city', 'town'])
                idx_listing_date = find_col_index(col_map, ['listing date', 'list date', 'date listed', 'listed date'])
                idx_price        = find_col_index(col_map, ['list price', 'listing price', 'price', 'asking'])
                idx_occupancy    = find_col_index(col_map, ['occupancy', 'occupied'])
                idx_agent_access = find_col_index(col_map, ['agent access', 'access'])

                # ── Parse data rows ──────────────────────────────────────────
                for row in data_rows:
                    if not row:
                        continue
                    non_empty = sum(1 for c in row if c and str(c).strip())
                    if non_empty < 3:
                        skipped['empty'] += 1
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

                        # Sanity gates (kept from v2)
                        if _looks_like_merged_cell_row(
                                [reo_status, financing, address1, address2, city]):
                            skipped['merged_cell'] += 1
                            print(f'[skip] merged-cell row: {address1!r}')
                            continue

                        if address1 and not _has_street_number(address1):
                            skipped['no_street_num'] += 1
                            print(f'[skip] no street number: {address1!r}')
                            continue

                        if address1 and city and address1.strip() == city.strip():
                            skipped['addr_eq_city'] += 1
                            print(f'[skip] addr == city: {address1!r}')
                            continue

                        if not address1 or not city:
                            skipped['no_addr_or_city'] += 1
                            continue

                        # Build full address
                        full_address = address1
                        if address2 and address2 not in ('.', '') and address2 != address1:
                            full_address += f' {address2}'
                        full_address += f', {city}'

                        listing_date = parse_listing_date(listing_date_raw)

                        price_clean = None
                        if list_price_s:
                            price_match = re.search(r'\$\s*[\d,]+', list_price_s)
                            if price_match:
                                price_clean = float(
                                    price_match.group(0).replace('$', '').replace(',', '').strip()
                                )

                        properties.append({
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
                            'listing_date': listing_date,
                        })

                    except Exception as e:
                        print(f'Error parsing row on page {page_num}: {e}')
                        continue

    if any(skipped.values()):
        print('[parser v3] skipped rows: ' +
              ', '.join(f'{k}={v}' for k, v in skipped.items() if v))
    print(f'[parser v3] accepted {len(properties)} rows')
    return properties


def get_database_properties():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, address, current_list_price, current_status, created_at,
               listing_date, last_activity_date, data_source
        FROM properties ORDER BY id
    """)
    properties = cur.fetchall()
    for prop in properties:
        prop['address_normalized'] = normalize_address(prop['address'])
        prop['street_number'] = extract_street_number(prop['address'])
    cur.close()
    conn.close()
    return properties


def find_matching_property(act_prop, db_properties):
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
    print('=' * 80)
    print('ACT SPREADSHEET RECONCILIATION (parser v3 — header carry-over)')
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
    results = {'matched': [], 'in_act_not_db': [], 'in_db_not_act': [],
               'timestamp': datetime.now().isoformat()}
    matched_db_ids = set()

    for act_prop in act_properties:
        match, confidence = find_matching_property(act_prop, db_properties)
        if match:
            results['matched'].append({
                'act_address': act_prop['address'], 'db_address': match['address'],
                'db_id': match['id'], 'confidence': confidence,
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
                'db_id': db_prop['id'], 'address': db_prop['address'],
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
