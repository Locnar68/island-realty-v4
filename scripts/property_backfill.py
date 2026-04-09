#!/usr/bin/env python3
"""
Property Data Backfill from Gmail - April 2026
Scans Gmail newest-first for each property and restores:
  - current_list_price
  - hold_harmless_required
  - financing_type
  - agent_access
  - occupancy_status
  - seller_agent_compensation
Stops at the first email that has useful data (newest = most current).
"""
import os, sys, pickle, base64, re
sys.path.insert(0, '/opt/island-realty')
sys.path.insert(0, '/opt/island-realty/app')
from dotenv import load_dotenv
load_dotenv('/opt/island-realty/.env')

from googleapiclient.discovery import build
from bs4 import BeautifulSoup
import psycopg2
from psycopg2.extras import RealDictCursor

# ── Auth ──────────────────────────────────────────────────────────────────────
with open('/opt/island-realty/config/token.pickle', 'rb') as f:
    creds = pickle.load(f)
gmail = build('gmail', 'v1', credentials=creds)

conn = psycopg2.connect(dbname='island_properties', user='island_user',
                        password=os.getenv('DB_PASSWORD', 'Pepmi@12'), host='localhost')
cur = conn.cursor(cursor_factory=RealDictCursor)

# ── Text extraction ───────────────────────────────────────────────────────────
def extract_text(payload):
    parts = []
    def walk(p):
        mt = p.get('mimeType', '')
        if mt in ('text/plain', 'text/html'):
            data = p.get('body', {}).get('data', '')
            if data:
                raw = base64.urlsafe_b64decode(data).decode('utf-8', 'ignore')
                parts.append(BeautifulSoup(raw, 'html.parser').get_text() if mt == 'text/html' else raw)
        for sub in p.get('parts', []):
            walk(sub)
    walk(payload)
    return '\n'.join(parts)

# ── Field extractors ──────────────────────────────────────────────────────────
PRICE_RE = re.compile(
    r'\$\s*([\d,]+(?:\.\d{2})?)'
    r'|(?:list\s*price|asking\s*price|new\s*list\s*price|price\s*reduction\s*to)[:\s]+\$?\s*([\d,]+)',
    re.IGNORECASE
)

def extract_price(text):
    for m in PRICE_RE.finditer(text):
        raw = (m.group(1) or m.group(2) or '').replace(',', '')
        try:
            val = float(raw)
            if 10000 < val < 5000000:
                return val
        except:
            pass
    return None

def extract_hold_harmless(text):
    if re.search(r'hold\s*harmless\s*required|must\s*sign.*hold\s*harmless|hold\s*harmless.*required', text, re.I):
        return True
    if re.search(r'hold\s*harmless\s*not\s*required|no\s*hold\s*harmless', text, re.I):
        return False
    return None

def extract_financing(text):
    if re.search(r'\bcash\s*only\b', text, re.I):
        return 'Cash Only'
    if re.search(r'\bconventional\b', text, re.I):
        return 'Conventional'
    if re.search(r'\bfha\b', text, re.I):
        return 'FHA'
    if re.search(r'\b203k\b', text, re.I):
        return '203K'
    if re.search(r'\bcash\s*or\s*conventional\b', text, re.I):
        return 'Cash or Conventional'
    return None

def extract_occupancy(text):
    if re.search(r'\bvacant\b', text, re.I):
        return 'Vacant'
    if re.search(r'\boccupied\b', text, re.I):
        return 'Occupied'
    return None

def extract_agent_access(text):
    m = re.search(r'(?:agent\s*access|showing\s*instructions?|access\s*info)[:\s]+([^\n]{5,80})', text, re.I)
    return m.group(1).strip() if m else None

def extract_seller_comp(text):
    m = re.search(r'(?:seller\s*(?:agent\s*)?comp(?:ensation)?|co-?op\s*fee)[:\s]+([^\n]{2,40})', text, re.I)
    return m.group(1).strip() if m else None

# ── Address helpers ───────────────────────────────────────────────────────────
def street_number(addr):
    m = re.match(r'^(\d+[\-/]?\d*)', addr.strip())
    return m.group(1) if m else None

def street_keywords(addr):
    s = re.sub(r'\b(unit|apt|apartment|#)\s*[\w\d]+', '', addr, flags=re.I)
    s = re.sub(r'[,.]', '', s).strip().lower()
    num = street_number(s)
    if num:
        s = s[len(num):].strip()
    s = re.sub(r'\b(street|st|avenue|ave|road|rd|blvd|boulevard|lane|ln|drive|dr|court|ct|place|pl|ny)\b', '', s, flags=re.I)
    words = [w for w in s.split() if len(w) > 2]
    return (num or '') + ' ' + ' '.join(words[:2])

# ── Load all active properties ────────────────────────────────────────────────
cur.execute("""
    SELECT id, address, city, current_status,
           current_list_price, hold_harmless_required,
           financing_type, agent_access, occupancy_status,
           seller_agent_compensation
    FROM properties
    WHERE is_active = TRUE
    ORDER BY id
""")
props = cur.fetchall()
print(f"Total active properties: {len(props)}")

updated = 0
skipped = 0

for prop in props:
    addr    = prop['address']
    prop_id = prop['id']

    # Determine what fields are still missing
    needs_price     = not prop['current_list_price'] or prop['current_list_price'] == 0
    needs_hh        = prop['hold_harmless_required'] is None
    needs_financing = not prop['financing_type']
    needs_access    = not prop['agent_access']
    needs_occupancy = not prop['occupancy_status']

    if not any([needs_price, needs_hh, needs_financing, needs_access, needs_occupancy]):
        continue  # property already has all data

    try:
        res = gmail.users().messages().list(
            userId='me',
            q=street_keywords(addr),
            maxResults=20
        ).execute()
    except Exception as e:
        print(f"  Gmail error [{addr[:40]}]: {e}")
        skipped += 1
        continue

    messages = res.get('messages', [])
    if not messages:
        skipped += 1
        continue

    # Collect best values found across emails (newest first)
    found = {}

    for msg_ref in messages:
        # Stop early if we have everything
        if not any([
            needs_price and 'price' not in found,
            needs_hh and 'hh' not in found,
            needs_financing and 'financing' not in found,
            needs_occupancy and 'occupancy' not in found,
        ]):
            break

        try:
            msg = gmail.users().messages().get(
                userId='me', id=msg_ref['id'], format='full'
            ).execute()
        except:
            continue

        headers = {h['name'].lower(): h['value'] for h in msg['payload']['headers']}
        subject = headers.get('subject', '')
        body    = extract_text(msg['payload'])
        text    = subject + '\n' + body

        # Street number sanity check
        sn = street_number(addr)
        if sn and sn not in text:
            continue

        if needs_price and 'price' not in found:
            p = extract_price(text)
            if p:
                found['price'] = p
                found['price_subj'] = subject[:60]

        if needs_hh and 'hh' not in found:
            hh = extract_hold_harmless(text)
            if hh is not None:
                found['hh'] = hh

        if needs_financing and 'financing' not in found:
            fin = extract_financing(text)
            if fin:
                found['financing'] = fin

        if needs_occupancy and 'occupancy' not in found:
            occ = extract_occupancy(text)
            if occ:
                found['occupancy'] = occ

        if needs_access and 'access' not in found:
            acc = extract_agent_access(text)
            if acc:
                found['access'] = acc

    if not found:
        skipped += 1
        continue

    # Build UPDATE
    sets, vals = [], []
    if 'price' in found:
        sets.append('current_list_price = %s')
        vals.append(found['price'])
    if 'hh' in found:
        sets.append('hold_harmless_required = %s')
        vals.append(found['hh'])
    if 'financing' in found:
        sets.append('financing_type = %s')
        vals.append(found['financing'])
    if 'occupancy' in found:
        sets.append('occupancy_status = %s')
        vals.append(found['occupancy'])
    if 'access' in found:
        sets.append('agent_access = %s')
        vals.append(found['access'])

    if sets:
        vals.append(prop_id)
        cur.execute(f"UPDATE properties SET {', '.join(sets)} WHERE id = %s", vals)
        conn.commit()
        updated += 1
        summary = []
        if 'price'     in found: summary.append(f"${found['price']:,.0f}")
        if 'hh'        in found: summary.append(f"HH={'Y' if found['hh'] else 'N'}")
        if 'financing' in found: summary.append(found['financing'])
        if 'occupancy' in found: summary.append(found['occupancy'])
        print(f"  OK  {addr[:50]:50s}  {' | '.join(summary)}")
    else:
        skipped += 1

cur.close()
conn.close()
print()
print(f"Done -- Updated: {updated}  |  Nothing found: {skipped}")