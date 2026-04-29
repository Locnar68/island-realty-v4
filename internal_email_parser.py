"""
internal_email_parser.py
=========================
Parses status/price-update emails from Island Advantage Realty agents.

Handles THREE templates (from 4 agents — Tina & Fernando share the firm template):

  Template 1 — Firm listing/price template (Tina, Fernando)
      Subject: "New List Price: <address>" or "Price reduction: <address>"
      Body: labeled table with __Address__/__List Price__ or
            "Property: / Previous $: / Current: $:" rows

  Template 2 — H&B announcement (Nikki)
      Subject: "<address>"  (address ONLY, no prefix)
      Body: "multiple-offer situation" / "Highest and Best offers by ..."

  Template 3 — Status/contract update (Claudia)
      Subject: "In Contract: <address>" or "Status Update- <status>"
      Body: "__Price__: $X", "__Close date__: M/D/YYYY", financing word

Entry point:
    parse_agent_email(subject, body, sender_email) -> dict | None

Returns None if the email should be ignored (wrong sender, no actionable
content, or content lives only in quoted reply history).

All regex extraction is deliberately tolerant — we extract a best-effort
address string and let the DB-layer ORDER BY LENGTH(address) DESC LIMIT 1
matcher pick the correct property card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Canonical status names (must match exactly — see memory: Status system)
# ---------------------------------------------------------------------------
STATUS_AVAILABLE        = "Available"
STATUS_AUCTION_AVAIL    = "Auction/Available"
STATUS_FIRST_ACCEPTED   = "1st Accepted"
STATUS_IN_CONTRACT      = "Incontract"
STATUS_HALF_SIGNED      = "\u00bd Signed"   # literal ½ character
STATUS_HANDB            = "H&B"
STATUS_TOTM             = "TOTM"
STATUS_SOLD             = "Sold"
STATUS_CLOSED           = "Closed"

# Statuses that are email-only (spreadsheet can never set these)
EMAIL_ONLY_STATUSES = frozenset({STATUS_HANDB})


# ---------------------------------------------------------------------------
# Agent whitelist (local-part @iarny.com)
# ---------------------------------------------------------------------------
AGENT_WHITELIST = frozenset({"nikki", "tina", "fernando", "claudia", "mickey"})
AGENT_DOMAIN = "iarny.com"


# ---------------------------------------------------------------------------
# Event types (internal — mapped to canonical status at the end)
# ---------------------------------------------------------------------------
EVT_NEW_LISTING      = "new_listing"       # Tina/Fernando "New List Price:"
EVT_PRICE_REDUCTION  = "price_reduction"   # Fernando "Price reduction:"
EVT_HANDB            = "handb"             # Nikki H&B announcement
EVT_FIRST_ACCEPTED   = "first_accepted"    # Claudia "1st accepted offer"
EVT_IN_CONTRACT      = "in_contract"       # Claudia "In Contract:"
EVT_HALF_SIGNED      = "half_signed"       # Claudia "fully executed" / ½ signed
EVT_AUCTION          = "auction"           # Claudia "Status Update- Auction"
EVT_SOLD             = "sold"              # sold/closed
EVT_CLOSED           = "closed"
EVT_BOM              = "bom"              # Claudia "BOM- Back on Market"


# ===========================================================================
# PUBLIC API
# ===========================================================================

@dataclass
class ParsedEmail:
    """Result of parsing an agent email."""
    event_type: str                    # EVT_* constant
    canonical_status: Optional[str]    # Target status, or None if price-only
    address_raw: Optional[str]         # Address text as seen in email
    address_normalized: Optional[str]  # Normalized form for DB lookup
    price: Optional[int]               # Integer dollars, or None
    financing: Optional[str]           # "CASH", "Cash/Conventional", etc.
    close_date: Optional[str]          # ISO date string YYYY-MM-DD, or None
    sender_agent: str                  # "nikki" | "tina" | "fernando" | "claudia"
    price_update_only: bool            # True when status should NOT change
    notes: Optional[str]               # Free-text notes (e.g., Tina violation info)
    confidence: str                    # "high" | "medium" | "low"

    def to_dict(self) -> dict:
        return asdict(self)


def parse_agent_email(subject: str, body: str, sender_email: str) -> Optional[ParsedEmail]:
    """
    Parse a single agent email. Returns ParsedEmail or None.

    Returns None when:
      - Sender is not one of the 4 whitelisted agents
      - No actionable event can be detected
      - The only actionable content is inside quoted reply history
    """
    # Step 1: Sender whitelist
    agent = _identify_agent(sender_email)
    if agent is None:
        return None

    # Normalize inputs
    subject = (subject or "").strip()
    body = body or ""

    # Step 2: Strip quoted reply history — anything we find must be in the
    # top (new) portion of the body, not inside a forwarded/quoted block
    fresh_body, quoted_body = _split_quoted_history(body)

    # Step 3: Detect event type from subject prefix (strongest signal).
    # Also remember whether this was a reply/forward — used below to guard
    # against re-firing announcement events on replies that merely quote them.
    had_reply_prefix = bool(_REPLY_PREFIX_RE.match(subject))
    event_type, subj_status_word = _classify_subject(subject)
    event_from_subject = event_type is not None

    # Step 4: Fall through to body phrases if no subject prefix match
    if event_type is None:
        event_type = _classify_body(fresh_body)

    # Step 5: If STILL nothing actionable, check whether the actionable
    # content is only in the quoted portion — if so, skip.
    if event_type is None:
        if _classify_body(quoted_body) is not None:
            return None
        return None

    # Step 5b: RE:/Fwd: guardrail. If the subject carries a reply prefix AND
    # the only reason we detected an event is the subject (not fresh-body
    # phrases), require at least one corroborating signal from the fresh
    # body — a body-phrase match OR a price. Without that, this is just a
    # reply echoing the original announcement; the ORIGINAL sender already
    # fired the event, and re-firing on a reply double-counts or worse,
    # clobbers a newer status with a stale one.
    if had_reply_prefix and event_from_subject:
        fresh_phrase = _classify_body(fresh_body) is not None
        fresh_price = _extract_price(fresh_body, event_type) is not None
        if not (fresh_phrase or fresh_price):
            return None

    # Step 6: Extract address
    address_raw, address_norm = _extract_address(subject, fresh_body, event_type)

    # Step 7: Extract price (event-specific strategy)
    price = _extract_price(fresh_body, event_type)

    # Step 8: Optional fields
    financing = _extract_financing(fresh_body)
    close_date = _extract_close_date(fresh_body)

    # Step 9: Map event -> canonical status
    canonical_status, price_update_only = _map_to_canonical(event_type, subj_status_word)

    # Step 10: Confidence scoring
    confidence = _score_confidence(event_type, address_norm, price, canonical_status)

    # Step 11: Capture free-text notes (violations, conditions, etc.)
    notes = _extract_notes(fresh_body)

    return ParsedEmail(
        event_type=event_type,
        canonical_status=canonical_status,
        address_raw=address_raw,
        address_normalized=address_norm,
        price=price,
        financing=financing,
        close_date=close_date,
        sender_agent=agent,
        price_update_only=price_update_only,
        notes=notes,
        confidence=confidence,
    )


# ===========================================================================
# STEP 1 — Sender identification
# ===========================================================================

_EMAIL_RE = re.compile(r"<?([^<>@\s]+)@([^<>@\s]+)>?")

def _identify_agent(sender_email: str) -> Optional[str]:
    """Return lowercase agent first-name if sender is whitelisted, else None."""
    if not sender_email:
        return None
    m = _EMAIL_RE.search(sender_email)
    if not m:
        return None
    local, domain = m.group(1).lower(), m.group(2).lower()
    if domain != AGENT_DOMAIN:
        return None
    if local not in AGENT_WHITELIST:
        return None
    return local


# ===========================================================================
# STEP 2 — Strip quoted reply/forward history
# ===========================================================================

# Delimiters that mark the start of a quoted reply block.
# Tried as a set — whichever matches earliest wins.
_QUOTE_DELIMITERS = [
    # Outlook / Gmail "From: <sender> Sent: <date>" block. From: and Sent:
    # labels can be on the same line (Outlook plain-text rendering) or
    # separate lines. DOTALL lets the gap span newlines.
    re.compile(r'(?ms)^\s*From:\s.{1,400}?Sent:\s', re.DOTALL),
    # Standard "On <date>, <n> wrote:"
    re.compile(r'(?ms)^\s*On\s+.{5,200}?\s+wrote:\s*$', re.DOTALL),
    # HTML blockquote
    re.compile(r'<blockquote', re.IGNORECASE),
    # "--- Original Message ---"
    re.compile(r'^\s*-{2,}\s*Original Message\s*-{2,}', re.MULTILINE | re.IGNORECASE),
    # "---- Forwarded message ----"
    re.compile(r'^\s*-{2,}\s*Forwarded message\s*-{2,}', re.MULTILINE | re.IGNORECASE),
]

def _split_quoted_history(body: str) -> tuple[str, str]:
    """Split body into (fresh, quoted). Returns earliest delimiter split."""
    if not body:
        return "", ""
    earliest = len(body)
    for pat in _QUOTE_DELIMITERS:
        m = pat.search(body)
        if m and m.start() < earliest:
            earliest = m.start()
    return body[:earliest], body[earliest:]


# ===========================================================================
# STEP 3 — Subject prefix classifier
# ===========================================================================

# Strip common reply/forward prefixes (RE:, Fwd:, etc.) to get at the core subject
_REPLY_PREFIX_RE = re.compile(
    r'^\s*(?:(?:RE|FW|FWD|Re|Fw|Fwd)\s*:\s*)+', re.IGNORECASE
)

_SUBJ_NEW_LIST = re.compile(r'^\s*New\s+List\s+Price\s*:\s*', re.IGNORECASE)
_SUBJ_PRICE_RED = re.compile(r'^\s*Price\s+reduction\s*:\s*', re.IGNORECASE)
_SUBJ_IN_CONTRACT = re.compile(r'^\s*In\s+Contract\s*:\s*', re.IGNORECASE)
_SUBJ_STATUS_UPDATE = re.compile(r'Status\s+Update\s*[-:]\s*(\w+)', re.IGNORECASE)
_SUBJ_BOM = re.compile(r'^\s*(?:BOM\s*[-:]?\s*)?Back\s+on\s+(?:the\s+)?Market', re.IGNORECASE)

def _classify_subject(subject: str) -> tuple[Optional[str], Optional[str]]:
    """
    Returns (event_type, captured_status_word) or (None, None).
    The captured_status_word is only populated for 'Status Update- X' subjects.
    """
    # Strip reply/forward prefix but remember the original for ordering signals
    core = _REPLY_PREFIX_RE.sub('', subject or '').strip()

    if _SUBJ_NEW_LIST.match(core):
        return EVT_NEW_LISTING, None
    if _SUBJ_PRICE_RED.match(core):
        return EVT_PRICE_REDUCTION, None
    if _SUBJ_IN_CONTRACT.match(core):
        return EVT_IN_CONTRACT, None
    if _SUBJ_BOM.search(core):
        return EVT_BOM, None

    m = _SUBJ_STATUS_UPDATE.search(core)
    if m:
        word = m.group(1).lower()
        if word.startswith('auction'):
            return EVT_AUCTION, word
        if word.startswith('sold'):
            return EVT_SOLD, word
        if word.startswith('closed'):
            return EVT_CLOSED, word
        # Unknown status word — let body classifier try
        return None, word

    return None, None


# ===========================================================================
# STEP 4 — Body phrase classifier (fallback)
# ===========================================================================

# Patterns that indicate specific events when subject doesn't classify
_BODY_HANDB = re.compile(
    r'(?:multiple[-\s]*offer\s+situation|highest\s+and\s+best\s+offers?\s+by)',
    re.IGNORECASE
)
_BODY_FIRST_ACCEPTED = re.compile(
    r'(?:1st\s+accepted\s+offer|first\s+accepted\s+offer|'
    r'now\s+has\s+(?:a\s+)?1st\s+accepted)',
    re.IGNORECASE
)
_BODY_HALF_SIGNED = re.compile(
    r'(?:fully\s+executed|\u00bd\s*signed|half[-\s]*signed|½\s*signed)',
    re.IGNORECASE
)
_BODY_IN_CONTRACT = re.compile(
    r'(?:^|\W)in\s+contract(?:\W|$)', re.IGNORECASE
)
_BODY_SOLD = re.compile(r'(?:has\s+closed|sale\s+closed|property\s+sold)', re.IGNORECASE)
_BODY_BOM = re.compile(r'(?:back\s+(?:on|to)\s+(?:the\s+)?(?:available|market)(?:\s+status)?)', re.IGNORECASE)

def _classify_body(body: str) -> Optional[str]:
    """Detect event type from body phrases."""
    if not body:
        return None
    if _BODY_HANDB.search(body):
        return EVT_HANDB
    if _BODY_FIRST_ACCEPTED.search(body):
        return EVT_FIRST_ACCEPTED
    if _BODY_HALF_SIGNED.search(body):
        return EVT_HALF_SIGNED
    if _BODY_IN_CONTRACT.search(body):
        return EVT_IN_CONTRACT
    if _BODY_SOLD.search(body):
        return EVT_SOLD
    if _BODY_BOM.search(body):
        return EVT_BOM
    return None


# ===========================================================================
# STEP 6 — Address extraction
# ===========================================================================

# Street type keywords — anchor for address parsing
_STREET_TYPES = [
    'Street', 'St', 'Avenue', 'Ave', 'Road', 'Rd', 'Boulevard', 'Blvd',
    'Drive', 'Dr', 'Place', 'Pl', 'Court', 'Ct', 'Lane', 'Ln',
    'Parkway', 'Pkwy', 'Way', 'Terrace', 'Ter', 'Highway', 'Hwy',
    'Turnpike', 'Tpke', 'Circle', 'Cir', 'Square', 'Sq', 'Plaza', 'Plz',
]
_STREET_TYPE_PATTERN = '(?:' + '|'.join(re.escape(t) for t in _STREET_TYPES) + r')\.?'

# Full address pattern:
#   <house number, optionally hyphenated> <street name words> <street type>
#   [unit] [city/neighborhood words] [state] [zip]
_ADDRESS_RE = re.compile(
    r'(?P<num>\d{1,5}(?:-\d{1,5})?)\s+'
    r'(?P<street>(?:[A-Za-z0-9][A-Za-z0-9\'\.]*\s+){1,4}?)'
    r'(?P<stype>' + _STREET_TYPE_PATTERN + r')'
    r'(?=\s|,|$|\n)',
    re.IGNORECASE
)

# Extended pattern: capture trailing unit/city/state/zip on same line
_ADDRESS_TAIL_RE = re.compile(
    r'(?P<unit>(?:\s+(?:Apt|Unit|Suite|Ste|#)\s*[A-Za-z0-9-]+))?'
    r'(?P<city>(?:\s+[A-Za-z][A-Za-z\.]*){0,4}?)'
    r'(?P<state>\s+NY)?'
    r'(?P<zip>\s+\d{5})?'
    r'(?:\s|,|$|\n)',
    re.IGNORECASE
)

# Common Long Island / NYC neighborhoods we expect — helps validate city
_KNOWN_LOCALITIES = {
    'jamaica', 'hicksville', 'islip', 'brooklyn', 'queens', 'bronx',
    'springfield gardens', 'jackson heights', 'center moriches',
    'hauppauge', 'smithtown', 'babylon', 'huntington', 'patchogue',
    'riverhead', 'southampton', 'massapequa', 'levittown', 'freeport',
    'hempstead', 'lindenhurst', 'bay shore', 'west babylon', 'amityville',
    'elmont', 'valley stream', 'uniondale', 'garden city', 'mineola',
    'farmingdale', 'bellport', 'medford', 'selden', 'centereach',
    'rockville centre', 'baldwin', 'roosevelt', 'ozone park',
    'st albans', 'saint albans', 'cambria heights', 'laurelton',
    'rosedale', 'south ozone park', 'east new york', 'flatbush',
    'canarsie', 'bedford stuyvesant', 'crown heights', 'bushwick',
}


def _extract_address(subject: str, body: str, event_type: str) -> tuple[Optional[str], Optional[str]]:
    """
    Try to extract an address. Strategy:
      1. For H&B emails, subject IS the address — try subject first
      2. For subject-prefix emails, subject AFTER the prefix has the address
      3. Fall back to scanning body
    Returns (raw_matched_string, normalized_string).
    """
    # Strip known prefixes from subject
    subj_core = _REPLY_PREFIX_RE.sub('', subject or '').strip()
    for pat in (_SUBJ_NEW_LIST, _SUBJ_PRICE_RED, _SUBJ_IN_CONTRACT):
        subj_core = pat.sub('', subj_core, count=1)
    subj_core = subj_core.strip()

    # Also remove "Status Update- X" because the address (if any) follows it
    subj_core = re.sub(r'Status\s+Update\s*[-:]\s*\w+\s*', '', subj_core, flags=re.IGNORECASE).strip()

    # Attempt 1: subject
    raw, norm = _find_address_in_text(subj_core)
    if raw:
        return raw, norm

    # Attempt 2: body — scan line by line, prefer lines that look like
    # standalone address lines (short-ish, contain a street type)
    if body:
        # For first-accepted emails, address often follows "1st accepted offer:"
        m = re.search(
            r'1st\s+accepted\s+offer\s*:?\s*\n?\s*([^\n]+)',
            body, re.IGNORECASE
        )
        if m:
            raw, norm = _find_address_in_text(m.group(1))
            if raw:
                return raw, norm

        # In firm-template emails, look for line wrapped in __ ... __ that
        # contains a street type — that's the address cell
        for m in re.finditer(r'__([^_\n]{10,120})__', body):
            candidate = m.group(1).strip()
            raw, norm = _find_address_in_text(candidate)
            if raw:
                return raw, norm

        # Claudia's "In Contract: ..." sometimes has the address inline with
        # "Please note the below referenced property" + next line
        m = re.search(
            r'below\s+referenced\s+property[^\n]*\n\s*([^\n]{8,120})',
            body, re.IGNORECASE
        )
        if m:
            raw, norm = _find_address_in_text(m.group(1))
            if raw:
                return raw, norm

        # Generic scan: first line containing a street type + digits
        for line in body.splitlines():
            line = line.strip()
            if not line or len(line) < 8 or len(line) > 150:
                continue
            if not re.search(r'\d', line):
                continue
            raw, norm = _find_address_in_text(line)
            if raw:
                return raw, norm

    return None, None


def _find_address_in_text(text: str) -> tuple[Optional[str], Optional[str]]:
    """Run address regex on one text fragment. Return (raw, normalized)."""
    if not text:
        return None, None
    text = text.strip().strip('_').strip()

    m = _ADDRESS_RE.search(text)
    if not m:
        return None, None

    # The core match: number + street name + street type
    start = m.start()
    core_end = m.end()
    core = text[start:core_end]

    # Try to capture trailing city/state/zip from the remainder of the line
    remainder = text[core_end:]
    tail_match = _ADDRESS_TAIL_RE.match(remainder)
    tail = ''
    if tail_match:
        tail = tail_match.group(0).rstrip(',').rstrip()

    raw = (core + tail).strip()
    # If the tail grabbed too much (e.g., a whole sentence), truncate at first comma or newline
    raw = re.split(r'[,\n]', raw, maxsplit=1)[0].strip()
    # Avoid runaway matches: cap at ~100 chars
    if len(raw) > 100:
        raw = raw[:100].rsplit(' ', 1)[0]

    norm = _normalize_address(raw)
    return raw, norm


# Canonical street-type map for normalization
_ST_ABBREV_MAP = {
    'st': 'Street', 'st.': 'Street', 'street': 'Street',
    'ave': 'Avenue', 'ave.': 'Avenue', 'avenue': 'Avenue',
    'rd': 'Road', 'rd.': 'Road', 'road': 'Road',
    'blvd': 'Boulevard', 'blvd.': 'Boulevard', 'boulevard': 'Boulevard',
    'dr': 'Drive', 'dr.': 'Drive', 'drive': 'Drive',
    'pl': 'Place', 'pl.': 'Place', 'place': 'Place',
    'ct': 'Court', 'ct.': 'Court', 'court': 'Court',
    'ln': 'Lane', 'ln.': 'Lane', 'lane': 'Lane',
    'pkwy': 'Parkway', 'pkwy.': 'Parkway', 'parkway': 'Parkway',
    'ter': 'Terrace', 'ter.': 'Terrace', 'terrace': 'Terrace',
    'hwy': 'Highway', 'hwy.': 'Highway', 'highway': 'Highway',
    'tpke': 'Turnpike', 'tpke.': 'Turnpike', 'turnpike': 'Turnpike',
}


def _normalize_address(raw: str) -> str:
    """Normalize an address for DB lookup.

    NOTE: We intentionally DO NOT expand abbreviations here. The DB stores
    addresses in their abbreviated form (e.g. '102 Hausch Blvd, Roosevelt')
    and a LIKE match must preserve that form. For matching robustness, the
    DB lookup layer tries multiple variants (see _address_lookup_variants).
    """
    if not raw:
        return raw
    s = re.sub(r'\s+', ' ', raw).strip().rstrip(',').strip()
    parts = s.split(' ')
    out = []
    for p in parts:
        low = p.lower().strip(',')
        if re.match(r'^\d+(st|nd|rd|th)$', low):
            out.append(low)
        elif re.match(r'^\d+(-\d+)?$', p):
            out.append(p)
        elif p.upper() == 'NY':
            out.append('NY')
        elif low in _ST_ABBREV_MAP:
            # Preserve the abbreviation form, just title-case it (Blvd not BLVD)
            out.append(p[0].upper() + p[1:].lower() if len(p) > 1 else p.upper())
        else:
            out.append(p.capitalize())
    return ' '.join(out)


# Mapping of full street types to common abbreviations (and vice versa)
_ABBREV_SWAPS = [
    ('Boulevard', 'Blvd'), ('Avenue', 'Ave'), ('Street', 'St'),
    ('Road', 'Rd'), ('Drive', 'Dr'), ('Place', 'Pl'),
    ('Court', 'Ct'), ('Lane', 'Ln'), ('Parkway', 'Pkwy'),
    ('Terrace', 'Ter'), ('Highway', 'Hwy'), ('Turnpike', 'Tpke'),
]


def _address_lookup_variants(addr):
    """Yield the input address plus each abbrev-swapped variant.

    For LIKE matching: if parser yields 'Hausch Blvd' but DB has 'Hausch Boulevard'
    (or vice versa), try both forms. Returns a list of unique candidates.
    """
    if not addr:
        return []
    out = [addr]
    seen = {addr}
    for full, abbr in _ABBREV_SWAPS:
        for fa, fb in [(full, abbr), (abbr, full)]:
            pat = re.compile(r'\b' + re.escape(fa) + r'\b', re.IGNORECASE)
            new = pat.sub(fb, addr)
            if new != addr and new not in seen:
                out.append(new)
                seen.add(new)
    return out


# ===========================================================================
# STEP 7 — Price extraction
# ===========================================================================

# Matches $629,000, $ 575,000, 629000, 629,000.00, etc.
_PRICE_RE = re.compile(r'\$?\s*([\d]{1,3}(?:,\d{3})+|\d{4,7})(?:\.\d{2})?')


def _extract_price(body: str, event_type: str) -> Optional[int]:
    """
    Event-specific price extraction:
      - PRICE_REDUCTION: anchor on 'Current' label, never grab 'Previous $:'
      - NEW_LISTING: prefer value near 'List Price' label
      - IN_CONTRACT: prefer value after __Price__ label
      - HANDB/FIRST_ACCEPTED/etc: no price extraction needed (preserve existing)
    """
    if not body:
        return None

    if event_type == EVT_PRICE_REDUCTION:
        # Look for "Current: $:" or "Current $:" followed by value (may be on next line)
        m = re.search(
            r'Current\s*[:$]+\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            body, re.IGNORECASE
        )
        if m:
            return _parse_price_str(m.group(1))
        # Fallback: "Current" label alone, then next line has value
        m = re.search(
            r'Current[^\n]*\n\s*\$?\s*([\d,]+(?:\.\d{2})?)',
            body, re.IGNORECASE
        )
        if m:
            return _parse_price_str(m.group(1))
        # Last resort: find all prices, assume Previous is first, Current second
        prices = _PRICE_RE.findall(body)
        if len(prices) >= 2:
            return _parse_price_str(prices[1])
        return None

    if event_type == EVT_NEW_LISTING:
        # Try "List Price" label
        m = re.search(
            r'(?:__)?List\s+Price(?:__)?\s*:?\s*\n?\s*\$\s*([\d,]+)',
            body, re.IGNORECASE
        )
        if m:
            return _parse_price_str(m.group(1))
        # Fallback: first $-prefixed price in body
        m = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', body)
        if m:
            return _parse_price_str(m.group(1))
        return None

    if event_type == EVT_IN_CONTRACT:
        # __Price__: $ 575,000
        m = re.search(
            r'(?:__)?Price(?:__)?\s*:\s*\$\s*([\d,]+)',
            body, re.IGNORECASE
        )
        if m:
            return _parse_price_str(m.group(1))
        # First $-prefixed price
        m = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', body)
        if m:
            return _parse_price_str(m.group(1))
        return None

    # Other events: no price extraction
    return None


def _parse_price_str(s: str) -> Optional[int]:
    """Parse '629,000' / '629000' / '629,000.00' to integer 629000."""
    if not s:
        return None
    cleaned = s.replace(',', '').replace('$', '').strip()
    # Drop decimals (cents)
    cleaned = cleaned.split('.')[0]
    try:
        val = int(cleaned)
    except ValueError:
        return None
    # Sanity bounds: residential property price between $10K and $50M
    if val < 10_000 or val > 50_000_000:
        return None
    return val


# ===========================================================================
# STEP 8 — Optional fields: financing & close date
# ===========================================================================

_FINANCING_RE = re.compile(
    r'\b(Cash/Rehab|Cash/Conventional|Conventional/Cash|'
    r'FHA/VA|FHA|VA|Conventional|CASH|Cash)\b',
    re.IGNORECASE
)

def _extract_financing(body: str) -> Optional[str]:
    if not body:
        return None
    m = _FINANCING_RE.search(body)
    if not m:
        return None
    return m.group(1).strip()


_CLOSE_DATE_RE = re.compile(
    r'(?:__)?Close\s+date(?:__)?\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})',
    re.IGNORECASE
)

def _extract_close_date(body: str) -> Optional[str]:
    if not body:
        return None
    m = _CLOSE_DATE_RE.search(body)
    if not m:
        return None
    raw = m.group(1)
    for fmt in ('%m/%d/%Y', '%m/%d/%y'):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


# ===========================================================================
# STEP 9 — Map event to canonical status
# ===========================================================================

def _map_to_canonical(event_type: str, status_word: Optional[str]) -> tuple[Optional[str], bool]:
    """
    Return (canonical_status, price_update_only).

    price_update_only=True means the status should NOT change — only price/
    other fields get updated. This covers New List Price on an already-active
    listing and all Price reduction emails.
    """
    if event_type == EVT_NEW_LISTING:
        # Downstream code decides: if address is new, set Available;
        # if address already active, just update price. Signal via flag.
        # We default to Available here; price_update_only=False unless the
        # DB layer confirms it's an existing active property.
        return STATUS_AVAILABLE, False

    if event_type == EVT_PRICE_REDUCTION:
        # Never change status — only update price
        return None, True

    if event_type == EVT_HANDB:
        return STATUS_HANDB, False
    if event_type == EVT_FIRST_ACCEPTED:
        return STATUS_FIRST_ACCEPTED, False
    if event_type == EVT_IN_CONTRACT:
        return STATUS_IN_CONTRACT, False
    if event_type == EVT_HALF_SIGNED:
        return STATUS_HALF_SIGNED, False
    if event_type == EVT_AUCTION:
        return STATUS_AUCTION_AVAIL, False
    if event_type == EVT_SOLD:
        return STATUS_SOLD, False
    if event_type == EVT_CLOSED:
        return STATUS_CLOSED, False
    if event_type == EVT_BOM:
        return STATUS_AVAILABLE, False

    return None, False


# ===========================================================================
# STEP 10 — Confidence scoring
# ===========================================================================

def _score_confidence(event_type: str, address_norm: Optional[str],
                      price: Optional[int], canonical_status: Optional[str]) -> str:
    """Rough confidence: high (address+price+status), medium (address+status), low (else)."""
    has_addr = bool(address_norm)
    has_price = price is not None
    has_status = canonical_status is not None or event_type == EVT_PRICE_REDUCTION

    if has_addr and has_status and (has_price or event_type in (EVT_HANDB, EVT_FIRST_ACCEPTED)):
        return 'high'
    if has_addr and has_status:
        return 'medium'
    return 'low'


# ===========================================================================
# STEP 11 — Free-text notes (for Tina's violation info, etc.)
# ===========================================================================

_NOTE_KEYWORDS = re.compile(
    r'(?:ECB\s+Violations?|HPD\s+Violations?|partial\s+vacate|'
    r'vacate\s+order|illegal\s+conversion|without\s+permit)',
    re.IGNORECASE
)

def _extract_notes(body: str) -> Optional[str]:
    """
    Pull out any free-text warnings about the property (violations, issues).
    Limited to first ~500 chars of the relevant paragraph.
    """
    if not body:
        return None
    if not _NOTE_KEYWORDS.search(body):
        return None
    # Find the sentence / paragraph containing the keyword
    # Return up to 500 chars around first match
    m = _NOTE_KEYWORDS.search(body)
    start = max(0, m.start() - 50)
    end = min(len(body), m.end() + 450)
    excerpt = body[start:end].strip()
    # Clean up excessive whitespace
    excerpt = re.sub(r'\n{3,}', '\n\n', excerpt)
    excerpt = re.sub(r'[ \t]+', ' ', excerpt)
    return excerpt[:500]


# ===========================================================================
# Backward-compat shim
# ===========================================================================
# The original strict-format parser expected:
#   STATUS: <status>
#   ADDRESS: <address>
#   PRICE: <int>
# If a message uses that exact format, parse_agent_email still handles it
# (the subject prefix detection won't match, but the body phrases might).
# For belt-and-braces, expose a legacy helper as well.

_STRICT_STATUS_RE = re.compile(r'^\s*STATUS\s*:\s*(.+?)\s*$', re.MULTILINE)
_STRICT_ADDRESS_RE = re.compile(r'^\s*ADDRESS\s*:\s*(.+?)\s*$', re.MULTILINE)
_STRICT_PRICE_RE = re.compile(r'^\s*PRICE\s*:\s*\$?\s*([\d,]+)\s*$', re.MULTILINE)


def parse_strict_format(body: str) -> Optional[dict]:
    """
    Parse the original strict STATUS:/ADDRESS:/PRICE: format.
    Returns dict with those keys or None. Kept for backward compatibility.
    """
    if not body:
        return None
    ms = _STRICT_STATUS_RE.search(body)
    ma = _STRICT_ADDRESS_RE.search(body)
    mp = _STRICT_PRICE_RE.search(body)
    if not (ms and ma):
        return None
    return {
        'status': ms.group(1).strip(),
        'address': ma.group(1).strip(),
        'price': _parse_price_str(mp.group(1)) if mp else None,
    }


# ===========================================================================
# LEGACY COMPATIBILITY SHIMS
# ===========================================================================
# The existing monitor_email_v4.py imports these three names:
#     from internal_email_parser import (
#         is_internal_email, parse_internal_email, apply_internal_update
#     )
# Signatures and return shapes below match the original module exactly so
# the monitor can upgrade without any code changes. The new 3-template parser
# activates when a `sender` is passed to parse_internal_email(); otherwise we
# fall back to the old strict STATUS:/ADDRESS:/PRICE: behavior.
# ===========================================================================

import logging as _logging
try:
    import psycopg2 as _psycopg2
    from psycopg2.extras import RealDictCursor as _RealDictCursor
except ImportError:
    _psycopg2 = None
    _RealDictCursor = None

_logger = _logging.getLogger(__name__)

# Original status normalization map — required for strict-format fallback
# (Rob's "Property Update:" emails use lowercase status words).
_STATUS_MAP = {
    'available': 'Available',
    'auction/available': 'Auction/Available',
    'auction available': 'Auction/Available',
    '1st accepted': '1st Accepted',
    'first accepted': '1st Accepted',
    'in contract': 'Incontract',
    'incontract': 'Incontract',
    'contract': 'Incontract',
    'sold': 'Sold',
    'closed': 'Closed',
    'h&b': 'H&B',
    'highest and best': 'H&B',
    'highest & best': 'H&B',
    'totm': 'TOTM',
    'temporarily off the market': 'TOTM',
    'bom': 'Available',
    'back on market': 'Available',
    'back on the market': 'Available',
}


# ---------------------------------------------------------------------------
# Extras extraction (agent_access, hold_harmless, auction.com signal)
# Used to populate columns that internal-path apply previously ignored.
# ---------------------------------------------------------------------------
_AUCTION_RE        = re.compile(r'auction\.com', re.IGNORECASE)
_HOLD_HARMLESS_RE  = re.compile(r'hold\s+harmless', re.IGNORECASE)
_OCCUPIED_RE       = re.compile(r'occupied(?:\s+do\s+not\s+disturb)?', re.IGNORECASE)
_LOCKBOX_RE        = re.compile(r'lock\s*box', re.IGNORECASE)
_VACANT_RE         = re.compile(r'\bvacant\b', re.IGNORECASE)


def _extract_listing_extras(body):
    """Pull a few non-status fields from the body of a new-listing email.

    Returns dict with keys:
      - agent_access:   'Lockbox' / 'Occupied. Do not disturb' / etc., or None
      - hold_harmless:  True if 'hold harmless' phrase appears, else False
      - auction_signal: True if body references auction.com (Rob Rule 2 trigger)
    """
    if not body:
        return {'agent_access': None, 'hold_harmless': False, 'auction_signal': False}

    auction = bool(_AUCTION_RE.search(body))
    hold    = bool(_HOLD_HARMLESS_RE.search(body))

    access = None
    if _LOCKBOX_RE.search(body):
        access = 'Lockbox'
    elif _OCCUPIED_RE.search(body):
        # Pull the actual phrase as Tina/Fernando wrote it for fidelity
        m = re.search(
            r'(occupied(?:[^\n.]{0,40})?)',
            body, re.IGNORECASE
        )
        if m:
            access = m.group(1).strip().rstrip('.').strip()[:120]
        else:
            access = 'Occupied'
    elif _VACANT_RE.search(body):
        access = 'Vacant'

    return {
        'agent_access':   access,
        'hold_harmless':  hold,
        'auction_signal': auction,
    }



def is_internal_email(subject, sender):
    """
    Gate: decide whether to attempt internal-email parsing at all.

    Matches the original behavior EXACTLY:
      - Subject starts with "property update:" (Rob's strict template), OR
      - Sender is any @iarny.com address (agent emails)
    """
    try:
        subj = (subject or '').lower().strip()
        frm = (sender or '').lower()
        return subj.startswith('property update:') or '@iarny.com' in frm
    except Exception:
        return False


def parse_internal_email(subject, body, sender=None):
    """
    Drop-in replacement for the original parse_internal_email().

    Original signature took (subject, body). We add an optional `sender`
    kwarg; when provided, we run the new 3-template parser that handles
    Tina/Fernando/Nikki/Claudia's natural email formats. When omitted, we
    fall back to the original strict STATUS:/ADDRESS:/PRICE: parser so
    Rob's template still works unchanged.

    Returns a dict shaped for the original apply_internal_update():
        {'status': <str or None>, 'address': <str>, 'price': <float or None>}
    ...or None if nothing actionable was found.

    When the new parser fires, extra metadata is included under underscore-
    prefixed keys for downstream logging/debugging — the original
    apply_internal_update() ignores unknown keys.
    """
    # Try the new agent-email parser first, but only if we have a sender.
    # Without a sender we can't enforce the agent whitelist safely.
    if sender:
        pe = parse_agent_email(subject, body, sender)
        if pe is not None:
            extras = _extract_listing_extras(body or '')
            canonical = pe.canonical_status
            # Rob's Rule 2 enforcement: NEW LISTING + auction.com signal -> Auction/Available
            if pe.event_type == EVT_NEW_LISTING and extras.get('auction_signal'):
                canonical = STATUS_AUCTION_AVAIL
            return {
                'status': canonical,                          # None for price_update_only
                'address': pe.address_normalized or pe.address_raw,
                'price': float(pe.price) if pe.price is not None else None,
                '_event_type': pe.event_type,
                '_price_update_only': pe.price_update_only,
                '_financing': pe.financing,
                '_close_date': pe.close_date,
                '_sender_agent': pe.sender_agent,
                '_notes': pe.notes,
                '_confidence': pe.confidence,
                '_agent_access': extras.get('agent_access'),
                '_hold_harmless': extras.get('hold_harmless'),
                '_auction_signal': extras.get('auction_signal'),
            }
        # New parser returned None — try strict format as a safety net
        # (e.g. Rob forwards from an @iarny.com address with the old template)

    # Strict STATUS:/ADDRESS:/PRICE: fallback — matches original behavior
    return _parse_strict_legacy(subject, body)


def _parse_strict_legacy(subject, body):
    """Original strict-format parser, preserved byte-for-byte."""
    result = {}
    if body:
        m = re.search(r'^STATUS\s*:\s*(.+)$', body, re.IGNORECASE | re.MULTILINE)
        if m:
            raw = m.group(1).strip()
            result['status'] = _STATUS_MAP.get(raw.lower(), raw)
        m = re.search(r'^ADDRESS\s*:\s*(.+)$', body, re.IGNORECASE | re.MULTILINE)
        if m:
            result['address'] = m.group(1).strip()
        m = re.search(r'^PRICE\s*:\s*([\d,\.]+)', body, re.IGNORECASE | re.MULTILINE)
        if m:
            try:
                result['price'] = float(m.group(1).replace(',', ''))
            except ValueError:
                pass
    # Subject-based address fallback: "Property Update: <addr>"
    if not result.get('address') and subject:
        m = re.match(r'property update:\s*(.+)', subject, re.IGNORECASE)
        if m:
            result['address'] = m.group(1).strip()
    # Original required BOTH address and status
    if not result.get('address') or not result.get('status'):
        return None
    return result


def apply_internal_update(parsed, email_id, email_subject, email_from, email_date):
    """
    Apply a parsed internal-email result to the database.

    Tries multiple address variants (Blvd / Boulevard etc.) for the LIKE
    match, canonicalizes the status via _STATUS_MAP, updates
    last_activity_date=NOW() any time the property is matched, leaves
    listing_date untouched (Rob's H&B rule), and writes agent_access /
    hold_harmless_required / current_list_price when those values were
    extracted from the body.

    Returns: {'property_id': int or None, 'actions': [str, ...]}
    """
    if _psycopg2 is None:
        _logger.error('apply_internal_update: psycopg2 not available')
        return {'property_id': None, 'actions': ['error:psycopg2_missing']}

    address = parsed.get('address')
    raw_status = parsed.get('status')
    new_status = _STATUS_MAP.get((raw_status or '').lower(), raw_status)
    new_price = parsed.get('price')

    agent_access = parsed.get('_agent_access')
    hold_harmless = parsed.get('_hold_harmless')
    event_type = parsed.get('_event_type')

    if not address:
        _logger.warning('apply_internal_update: no address in parsed result')
        return {'property_id': None, 'actions': ['no_address']}

    actions = []
    try:
        conn = _psycopg2.connect(
            host='localhost',
            dbname='island_properties',
            user='island_user',
            password='Pepmi@12',
        )
        cur = conn.cursor(cursor_factory=_RealDictCursor)

        # Try each abbreviation variant until we get a hit
        row = None
        matched_variant = None
        for variant in _address_lookup_variants(address):
            cur.execute(
                "SELECT id, current_status, current_list_price "
                "FROM properties "
                "WHERE LOWER(address) LIKE LOWER(%s) "
                "AND (is_active IS NULL OR is_active=TRUE) "
                "ORDER BY LENGTH(address) DESC LIMIT 1",
                ('%' + variant + '%',),
            )
            row = cur.fetchone()
            if row:
                matched_variant = variant
                break

        if not row:
            _logger.warning('apply_internal_update: no property found for %r '
                            '(variants tried: %s)',
                            address, _address_lookup_variants(address))
            conn.close()
            return {'property_id': None, 'actions': ['no_property_found']}

        prop_id = row['id']
        old_status = row['current_status']
        old_price = row['current_list_price']

        if matched_variant != address:
            actions.append('addr_variant:' + matched_variant)

        # ---- Status update ---------------------------------------------------
        if new_status and new_status != old_status:
            cur.execute(
                "UPDATE properties SET current_status=%s, "
                "status_source='email', "
                "last_activity_date=NOW(), updated_at=NOW() "
                "WHERE id=%s",
                (new_status, prop_id),
            )
            cur.execute(
                "INSERT INTO status_history "
                "(property_id, old_status, new_status, "
                "source_email_id, source_email_subject) "
                "VALUES (%s,%s,%s,%s,%s)",
                (prop_id, old_status, new_status, email_id, email_subject),
            )
            actions.append('status:' + str(old_status) + '->' + str(new_status))
            # If status flipped to Sold/Closed, enforce Rob Rule 5 (deactivate)
            if new_status in ('Sold', 'Closed'):
                cur.execute(
                    "UPDATE properties SET is_active=FALSE WHERE id=%s",
                    (prop_id,),
                )
                actions.append('deactivated')
        else:
            # Even with no status change, mark the activity date so Rob can
            # see the property had recent agent contact (H&B refresh case)
            cur.execute(
                "UPDATE properties SET last_activity_date=NOW(), "
                "updated_at=NOW() WHERE id=%s",
                (prop_id,),
            )
            actions.append('activity_touched')

        # ---- Price update ----------------------------------------------------
        if new_price and float(new_price) > 0:
            cur.execute(
                "UPDATE properties SET current_list_price=%s, "
                "updated_at=NOW() WHERE id=%s "
                "AND (current_list_price IS NULL OR current_list_price!=%s)",
                (new_price, prop_id, new_price),
            )
            if cur.rowcount > 0:
                actions.append('price:' + str(new_price))

        # ---- Agent access ----------------------------------------------------
        if agent_access:
            cur.execute(
                "UPDATE properties SET agent_access=%s, updated_at=NOW() "
                "WHERE id=%s "
                "AND (agent_access IS NULL OR agent_access!=%s)",
                (agent_access, prop_id, agent_access),
            )
            if cur.rowcount > 0:
                actions.append('agent_access:' + agent_access[:40])

        # ---- Hold harmless ---------------------------------------------------
        if hold_harmless is True:
            cur.execute(
                "UPDATE properties SET hold_harmless_required=TRUE, "
                "updated_at=NOW() WHERE id=%s "
                "AND (hold_harmless_required IS NULL "
                "OR hold_harmless_required=FALSE)",
                (prop_id,),
            )
            if cur.rowcount > 0:
                actions.append('hold_harmless:true')

        # ---- Email log -------------------------------------------------------
        cur.execute(
            "INSERT INTO property_emails "
            "(property_id, gmail_message_id, email_subject, "
            "email_body, email_from, email_date) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (gmail_message_id) DO NOTHING",
            (prop_id, email_id, email_subject, '', email_from, email_date),
        )

        conn.commit()
        conn.close()
        return {'property_id': prop_id, 'actions': actions or ['no_change']}

    except Exception as e:
        _logger.error('apply_internal_update error: ' + str(e), exc_info=True)
        return {'property_id': None, 'actions': ['error:' + str(e)]}
