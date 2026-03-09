"""
Enhanced property matching and email import logging
Insert these functions into monitor_email_v4.py to improve reliability
"""

import re


def normalize_address_for_matching(address):
    """
    Normalize address for better matching
    Returns: (street_number, street_name, city)
    """
    if not address:
        return (None, None, None)
    
    # Remove unit/apt suffixes
    addr = address.lower().strip()
    addr = re.sub(r'\s+(apt|unit|#|ste|suite)\s+.*$', '', addr, flags=re.IGNORECASE)
    
    # Extract components: [number] [street name] [city] [state] [zip]
    match = re.match(r'^([\d\-]+)\s+(.+?)\s+([a-z\s]+?)(?:\s+[a-z]{2})?\s*(?:\d{5})?$', addr, re.IGNORECASE)
    
    if match:
        street_num = match.group(1).strip()
        street_name = match.group(2).strip()
        city = match.group(3).strip()
        
        # Normalize street suffixes
        street_name = street_name.replace(' avenue', ' ave')
        street_name = street_name.replace(' street', ' st')
        street_name = street_name.replace(' road', ' rd')
        street_name = street_name.replace(' drive', ' dr')
        street_name = street_name.replace(' boulevard', ' blvd')
        
        return (street_num, street_name, city)
    
    return (None, addr, None)


def smart_property_match(cursor, address, city=None, mls_number=None):
    """
    Smart property matching with scoring
    
    Returns: property_id or None
    
    Matching strategy:
    - If MLS provided, match by MLS (exact)
    - Otherwise, score-based address matching:
      +2 for street number match (required)
      +2 for street name match
      +1 for city match
      Minimum score: 3 (street number + street name/city)
    """
    # Try MLS first
    if mls_number:
        cursor.execute("""
            SELECT id FROM properties WHERE mls_number = %s
        """, (mls_number,))
        result = cursor.fetchone()
        if result:
            return result['id']
    
    # No address - can't match
    if not address:
        return None
    
    # Normalize search address
    street_num, street_name, search_city = normalize_address_for_matching(address)
    
    if not street_num:
        # Can't match without street number
        return None
    
    # Get all properties for scoring
    cursor.execute("SELECT id, address FROM properties")
    properties = cursor.fetchall()
    
    best_match = None
    best_score = 0
    
    for prop in properties:
        score = 0
        prop_num, prop_name, prop_city = normalize_address_for_matching(prop['address'])
        
        # Street number MUST match
        if not prop_num or prop_num != street_num:
            continue
        
        score += 2  # Street number match
        
        # Street name match
        if prop_name and street_name:
            if prop_name == street_name:
                score += 2  # Exact match
            elif prop_name in street_name or street_name in prop_name:
                score += 1  # Partial match
        
        # City match (bonus)
        if prop_city and search_city and prop_city == search_city:
            score += 1
        elif city and prop_city and prop_city.lower() == city.lower():
            score += 1
        
        # Require minimum score of 3 (number + name or number + name_partial + city)
        if score >= 3 and score > best_score:
            best_score = score
            best_match = prop['id']
    
    return best_match


def log_email_import(cursor, email_data, extracted_data, property_id, success, error_message=None):
    """
    Log email import to email_import_log table for debugging
    """
    property_data = extracted_data.get('property_data', {}) if extracted_data else {}
    attachments = email_data.get('attachments', [])
    
    foil_count = sum(1 for att in attachments if att.get('is_foil', False))
    
    try:
        cursor.execute("""
            INSERT INTO email_import_log 
            (email_id, email_subject, email_date, parsed_address, parsed_mls,
             property_matched, property_id, attachments_found, attachments_saved,
             foil_count, error_message)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            email_data.get('id'),
            email_data.get('subject'),
            email_data.get('date'),
            property_data.get('address'),
            property_data.get('mls_number'),
            property_id is not None,
            property_id,
            len(attachments),
            len(attachments) if success else 0,
            foil_count,
            error_message
        ))
    except Exception as e:
        logger.warning(f"Failed to log email import: {e}")


def extract_address_from_subject(subject):
    """
    Extract address from email subject line
    Common patterns:
    - "New List Price: 140 Arlington Avenue Valley Stream NY 11580"
    - "Price Reduction: 123 Main St, Anytown NY"
    - "Origination: 456 Oak Drive, City, ST 12345"
    """
    # Try pattern: "[prefix:] [street address] [city] [state] [zip]"
    match = re.search(r':\s*(.+?)\s+([A-Z]{2})\s+(\d{5})', subject, re.IGNORECASE)
    if match:
        address_part = match.group(1).strip()
        state = match.group(2)
        zip_code = match.group(3)
        
        # Split into address and city
        parts = address_part.rsplit(',', 1)
        if len(parts) == 2:
            return f"{parts[0].strip()}, {parts[1].strip()}, {state} {zip_code}"
        else:
            return f"{address_part}, {state} {zip_code}"
    
    return None


# REPLACEMENT FOR _save_to_database function in monitor_email_v4.py
def _save_to_database_enhanced(self, email_data, extracted_data):
    """
    Enhanced version with:
    - Better address matching
    - Auto-create when no match found
    - Email import logging
    - Improved FOIL handling
    """
    actions = []
    property_id = None
    error_message = None
    
    with db_connection() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        try:
            property_data = extracted_data.get('property_data', {})
            
            if not property_data:
                log_email_import(cursor, email_data, extracted_data, None, False, "No property data extracted")
                return {'property_id': None, 'actions': ['no_property_data']}
            
            # 1. Find or create property using SMART MATCHING
            mls_number = property_data.get('mls_number')
            address = property_data.get('address')
            city = property_data.get('city')
            
            # If no address in extracted data, try to extract from subject
            if not address:
                address = extract_address_from_subject(email_data.get('subject', ''))
                if address:
                    property_data['address'] = address
                    actions.append('address_from_subject')
            
            # Try smart matching
            property_id = smart_property_match(cursor, address, city, mls_number)
            
            if property_id:
                actions.append(f'found_existing_{property_id}')
                logger.info(f"  Matched to existing property {property_id}")
            else:
                # NO MATCH - Create new property
                logger.info(f"  No match found, creating new property for: {address}")
                property_id = self._create_property(cursor, property_data, email_data)
                if property_id:
                    actions.append(f'created_property_{property_id}')
                else:
                    error_message = "Failed to create property"
                    log_email_import(cursor, email_data, extracted_data, None, False, error_message)
                    conn.commit()
                    return {'property_id': None, 'actions': ['create_failed']}
            
            # 2. Link email to property
            has_attachments = len(email_data.get('attachments', [])) > 0
            attachment_count = len(email_data.get('attachments', []))
            attachment_names = [a['filename'] for a in email_data.get('attachments', [])]
            
            cursor.execute("""
                INSERT INTO property_emails 
                (property_id, gmail_message_id, email_subject, email_body, email_from, email_date,
                 has_attachments, attachment_count, attachment_names)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (gmail_message_id) DO NOTHING
            """, (property_id, email_data['id'], email_data['subject'], 
                  email_data['body'], email_data['from'], email_data['date'],
                  has_attachments, attachment_count, attachment_names))
            actions.append('email_linked')
            
            # 3. Save attachments with PROPER FOIL CATEGORIZATION (PDF only)
            for att in email_data.get('attachments', []):
                # PDF-only policy: skip images and other non-PDF files
                _mime = att.get('mimeType', '')
                _fname = att.get('filename', '')
                if not (_mime == 'application/pdf' or _fname.lower().endswith('.pdf')):
                    logger.info(f"  ⏭️ Skipping non-PDF attachment: {_fname} ({_mime})")
                    continue

                is_foil = att.get('is_foil', False)
                
                # Determine category
                category = 'General'
                fname_lower = att['filename'].lower()
                
                if 'foil' in fname_lower:
                    category = 'FOIL'
                    is_foil = True
                elif any(x in fname_lower for x in ['violation', 'ecb']):
                    category = 'Violations'
                elif any(x in fname_lower for x in ['co ', 'tco', 'certificate']):
                    category = 'CO/TCO'
                elif 'inventory' in fname_lower:
                    category = 'Inventory'
                elif 'harmless' in fname_lower:
                    category = 'Hold Harmless'
                
                cursor.execute("""
                    INSERT INTO attachments 
                    (property_id, filename, file_size, mime_type, category, 
                     source_email_id, gmail_attachment_id, gmail_message_id, is_foil,
                     source_email_date, uploaded_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (property_id, att['filename'], att.get('size', 0), 
                      att.get('mimeType', 'application/octet-stream'),
                      category, email_data['id'], att.get('attachmentId'),
                      att.get('gmail_message_id', email_data['id']),
                      is_foil, email_data['date'], 'email_monitor'))
                
                actions.append(f'attachment:{category}:{att["filename"][:30]}')
                if is_foil:
                    logger.info(f"  🔴 FOIL attachment saved: {att['filename']}")
            
            # 4. Update property attachment counts
            if attachment_count > 0:
                cursor.execute("""
                    UPDATE properties SET 
                        has_attachments = TRUE,
                        attachment_count = (SELECT COUNT(*) FROM attachments WHERE property_id = %s),
                        last_email_id = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (property_id, email_data['id'], property_id))
            
            # 5. Handle status change
            status_change = extracted_data.get('status_change')
            if status_change and status_change.get('new_status'):
                new_status = status_change['new_status']
                cursor.execute("SELECT current_status FROM properties WHERE id = %s", (property_id,))
                current = cursor.fetchone()
                old_status = current['current_status'] if current else None
                
                if old_status != new_status:
                    cursor.execute("""
                        UPDATE properties SET 
                            current_status = %s, 
                            updated_at = NOW(),
                            last_email_id = %s
                        WHERE id = %s
                    """, (new_status, email_data['id'], property_id))
                    
                    cursor.execute("""
                        INSERT INTO status_history 
                        (property_id, old_status, new_status, source_email_id, source_email_subject)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (property_id, old_status, new_status, email_data['id'], email_data['subject']))
                    
                    actions.append(f'status:{old_status}->{new_status}')
            
            # 6. Update price if changed
            new_price = property_data.get('current_list_price')
            if new_price:
                cursor.execute("""
                    UPDATE properties SET 
                        current_list_price = %s,
                        updated_at = NOW()
                    WHERE id = %s AND (current_list_price IS NULL OR current_list_price != %s)
                """, (new_price, property_id, new_price))
                if cursor.rowcount > 0:
                    actions.append(f'price_updated:{new_price}')
            
            # Log successful import
            log_email_import(cursor, email_data, extracted_data, property_id, True, None)
            
            conn.commit()
            return {'property_id': property_id, 'actions': actions}
            
        except Exception as e:
            logger.error(f"Error saving to database: {e}")
            log_email_import(cursor, email_data, extracted_data, property_id, False, str(e))
            conn.rollback()
            return {'property_id': None, 'actions': ['error'], 'error': str(e)}

