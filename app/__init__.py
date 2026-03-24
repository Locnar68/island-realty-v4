from flask import Flask, jsonify, request, render_template
import psycopg2
import psycopg2.extras
import redis
import os
import pickle
import base64
from datetime import datetime
from googleapiclient.discovery import build
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

app = Flask(__name__)

# Database connection
def get_db():
    conn = psycopg2.connect(
        dbname='island_properties',
        user='island_user',
        password=os.getenv('DB_PASSWORD', 'Pepmi@12'),
        host='localhost'
    )
    return conn

# Gmail service
def get_gmail_service():
    TOKEN_FILE = '/opt/island-realty/config/token.pickle'
    with open(TOKEN_FILE, 'rb') as token:
        creds = pickle.load(token)
    return build('gmail', 'v1', credentials=creds)

# Redis connection
r = redis.Redis(host='localhost', port=6379, decode_responses=True)

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/health')
def health():
    checks = {
        "api": "healthy",
        "database": "unknown",
        "redis": "unknown"
    }
    
    try:
        conn = get_db()
        conn.close()
        checks["database"] = "connected"
    except Exception as e:
        checks["database"] = f"error: {str(e)}"
    
    try:
        r.ping()
        checks["redis"] = "connected"
    except Exception as e:
        checks["redis"] = f"error: {str(e)}"
    
    return jsonify(checks)

@app.route('/api/properties')
def properties():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Check if admin view requested (shows TOTM properties too)
        show_totm = request.args.get('show_totm', 'false').lower() == 'true'
        
        if show_totm:
            # Admin view: show all properties including TOTM
            cur.execute("""
                SELECT p.id, p.mls_number, p.address, p.address_2, p.current_list_price, 
                       p.status, p.current_status, p.created_at, p.updated_at, 
                       p.has_attachments, p.attachment_count, p.gmail_message_id,
                       p.financing_type, p.agent_access, p.seller_agent_compensation,
                       p.occupancy_status, p.hold_harmless_required,
                       p.property_type, p.reo_status, p.highest_best_due_at, p.totm_since, p.primary_photo_url,
                       (SELECT COUNT(*) FROM attachments a WHERE a.property_id = p.id AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf')) as total_attachments,
                       (SELECT COUNT(*) FROM attachments a WHERE a.property_id = p.id AND a.is_foil = TRUE AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf')) as foil_count,
                       (SELECT a.id FROM attachments a WHERE a.property_id = p.id AND a.category = 'Hold Harmless' AND a.gmail_attachment_id IS NOT NULL AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf') ORDER BY a.uploaded_at DESC LIMIT 1) as hh_attachment_id,
                       (SELECT a.id FROM attachments a WHERE a.property_id = p.id AND a.is_foil = TRUE AND a.gmail_attachment_id IS NOT NULL AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf') ORDER BY a.uploaded_at DESC LIMIT 1) as foil_attachment_id
                FROM properties p
                WHERE (p.is_active IS NULL OR p.is_active = TRUE)
                ORDER BY p.updated_at DESC
            """)
        else:
            # Public view: hide TOTM properties, and hide properties TOTM > 2 weeks
            cur.execute("""
                SELECT p.id, p.mls_number, p.address, p.address_2, p.current_list_price, 
                       p.status, p.current_status, p.created_at, p.updated_at, 
                       p.has_attachments, p.attachment_count, p.gmail_message_id,
                       p.financing_type, p.agent_access, p.seller_agent_compensation,
                       p.occupancy_status, p.hold_harmless_required,
                       p.property_type, p.reo_status, p.highest_best_due_at, p.totm_since, p.primary_photo_url,
                       (SELECT COUNT(*) FROM attachments a WHERE a.property_id = p.id AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf')) as total_attachments,
                       (SELECT COUNT(*) FROM attachments a WHERE a.property_id = p.id AND a.is_foil = TRUE AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf')) as foil_count,
                       (SELECT a.id FROM attachments a WHERE a.property_id = p.id AND a.category = 'Hold Harmless' AND a.gmail_attachment_id IS NOT NULL AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf') ORDER BY a.uploaded_at DESC LIMIT 1) as hh_attachment_id,
                       (SELECT a.id FROM attachments a WHERE a.property_id = p.id AND a.is_foil = TRUE AND a.gmail_attachment_id IS NOT NULL AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf') ORDER BY a.uploaded_at DESC LIMIT 1) as foil_attachment_id
                FROM properties p
                WHERE p.current_status != 'TOTM'
                  AND (p.is_active IS NULL OR p.is_active = TRUE)
                ORDER BY p.updated_at DESC
            """)
        
        rows = cur.fetchall()
        
        properties_list = []
        for row in rows:
            properties_list.append({
                "id": row['id'],
                "mls_number": row['mls_number'],
                "address": row['address'],
                "address_2": row.get('address_2') or '',
                "current_list_price": float(row['current_list_price']) if row['current_list_price'] else 0,
                "price": float(row['current_list_price']) if row['current_list_price'] else 0,
                "status": row['status'],
                "current_status": row['current_status'],
                "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None,
                "has_attachments": row['has_attachments'] or False,
                "attachment_count": row['total_attachments'] or 0,
                "gmail_message_id": row['gmail_message_id'],
                "financing_type": row['financing_type'],
                "agent_access": row['agent_access'],
                "seller_agent_compensation": row['seller_agent_compensation'],
                "occupancy_status": row['occupancy_status'],
                "hold_harmless_required": row['hold_harmless_required'] or False,
                "property_type": row['property_type'],
                "reo_status": row['reo_status'],
                "foil_count": row['foil_count'] or 0,
                "total_attachments": row['total_attachments'] or 0,
                "hh_attachment_id": row['hh_attachment_id'],
                "foil_attachment_id": row['foil_attachment_id'],
                "highest_best_due_at": row['highest_best_due_at'].isoformat() if row.get('highest_best_due_at') else None,
                "totm_since": row['totm_since'].isoformat() if row.get('totm_since') else None,
                "primary_photo_url": row.get('primary_photo_url') or None
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "count": len(properties_list),
            "properties": properties_list
        })
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "count": 0,
            "properties": []
        })

@app.route('/api/properties/<int:property_id>/emails')
def property_emails(property_id):
    """Get all emails for a property"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("""
            SELECT id, gmail_message_id, email_subject, email_from, email_date,
                   has_attachments, attachment_count, attachment_names
            FROM property_emails
            WHERE property_id = %s
            ORDER BY email_date DESC, created_at DESC
        """, (property_id,))
        
        emails = cur.fetchall()
        
        result = []
        for email in emails:
            result.append({
                "id": email['id'],
                "gmail_message_id": email['gmail_message_id'],
                "email_subject": email['email_subject'],
                "email_from": email['email_from'],
                "email_date": email['email_date'].isoformat() if email['email_date'] else None,
                "has_attachments": email['has_attachments'] or False,
                "attachment_count": email['attachment_count'] or 0,
                "attachment_names": email['attachment_names'] or []
            })
        
        cur.close()
        conn.close()
        
        return jsonify({"emails": result})
        
    except Exception as e:
        return jsonify({"error": str(e), "emails": []})

@app.route('/api/properties/<int:property_id>/attachments')
def property_attachments(property_id):
    """Get all attachments for a property across all emails"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("""
            SELECT a.id, a.filename, a.file_size, a.mime_type, a.category,
                   a.source_email_id, a.gmail_attachment_id, a.gmail_message_id,
                   a.is_foil, a.source_email_date, a.uploaded_at,
                   pe.email_subject
            FROM attachments a
            LEFT JOIN property_emails pe ON pe.gmail_message_id = a.gmail_message_id
            WHERE a.property_id = %s
              AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf')
            ORDER BY a.is_foil DESC, a.uploaded_at DESC
        """, (property_id,))
        
        attachments = cur.fetchall()
        
        result = []
        for att in attachments:
            result.append({
                "id": att['id'],
                "filename": att['filename'],
                "file_size": att['file_size'],
                "mime_type": att['mime_type'],
                "category": att['category'],
                "gmail_attachment_id": att['gmail_attachment_id'],
                "gmail_message_id": att['gmail_message_id'],
                "is_foil": att['is_foil'] or False,
                "source_email_date": att['source_email_date'].isoformat() if att['source_email_date'] else None,
                "email_subject": att['email_subject']
            })
        
        cur.close()
        conn.close()
        
        return jsonify({
            "property_id": property_id,
            "total": len(result),
            "foil_count": sum(1 for a in result if a['is_foil']),
            "attachments": result
        })
        
    except Exception as e:
        return jsonify({"error": str(e), "attachments": []})

@app.route('/api/forward-attachments', methods=['POST'])
def forward_all_attachments():
    """Forward ALL attachments for a property to an agent email.
    Collects attachments across all emails for the property."""
    try:
        data = request.json
        property_id = data.get('property_id')
        agent_email = data.get('agent_email')
        
        if not property_id or not agent_email:
            return jsonify({"error": "property_id and agent_email are required"}), 400
        
        # Get property info
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("SELECT mls_number, address FROM properties WHERE id = %s", (property_id,))
        prop = cur.fetchone()
        if not prop:
            return jsonify({"error": "Property not found"}), 404
        
        # Get all attachments for this property
        cur.execute("""
            SELECT DISTINCT a.gmail_attachment_id, a.gmail_message_id, a.filename, 
                   a.mime_type, a.is_foil, a.category
            FROM attachments a
            WHERE a.property_id = %s 
              AND a.gmail_attachment_id IS NOT NULL
            ORDER BY a.is_foil DESC, a.filename
        """, (property_id,))
        
        attachments = cur.fetchall()
        cur.close()
        conn.close()
        
        if not attachments:
            return jsonify({"error": "No attachments found for this property"}), 404
        
        # Get Gmail service
        service = get_gmail_service()
        
        # Create the forwarding email
        forward = MIMEMultipart()
        forward['to'] = agent_email
        forward['subject'] = f"Property Attachments: {prop['address']} (MLS: {prop['mls_number'] or 'N/A'})"
        
        # Build body listing all attachments
        foil_count = sum(1 for a in attachments if a['is_foil'])
        body_lines = [
            f"Property: {prop['address']}",
            f"MLS: {prop['mls_number'] or 'N/A'}",
            f"",
            f"Total Attachments: {len(attachments)}",
        ]
        if foil_count > 0:
            body_lines.append(f"FOIL Documents: {foil_count}")
        body_lines.append("")
        body_lines.append("Attached files:")
        for att in attachments:
            foil_tag = " [FOIL]" if att['is_foil'] else ""
            body_lines.append(f"  • {att['filename']}{foil_tag} ({att['category']})")
        body_lines.append("")
        body_lines.append("---")
        body_lines.append("Sent from Island Advantage Property Management System")
        
        forward.attach(MIMEText('\n'.join(body_lines), 'plain'))
        
        # Download and attach each file from Gmail
        attached_count = 0
        errors = []
        
        for att in attachments:
            try:
                gmail_att = service.users().messages().attachments().get(
                    userId='me',
                    messageId=att['gmail_message_id'],
                    id=att['gmail_attachment_id']
                ).execute()
                
                file_data = base64.urlsafe_b64decode(gmail_att['data'])
                
                mime_part = MIMEBase('application', 'octet-stream')
                mime_part.set_payload(file_data)
                encoders.encode_base64(mime_part)
                mime_part.add_header(
                    'Content-Disposition',
                    f'attachment; filename="{att["filename"]}"'
                )
                forward.attach(mime_part)
                attached_count += 1
                
            except Exception as e:
                errors.append(f"Failed to get {att['filename']}: {str(e)}")
        
        if attached_count == 0:
            return jsonify({"error": "Could not retrieve any attachments from Gmail", "details": errors}), 500
        
        # Send the email
        raw_message = base64.urlsafe_b64encode(forward.as_bytes()).decode()
        sent = service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()
        
        return jsonify({
            "success": True,
            "message_id": sent['id'],
            "property": prop['address'],
            "forwarded_to": agent_email,
            "attachments_sent": attached_count,
            "foil_documents": foil_count,
            "errors": errors if errors else None
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/property/<int:property_id>/forward-foil', methods=['POST'])
def forward_foil_documents(property_id):
    """Forward only FOIL attachments for a property to an agent email."""
    try:
        data = request.json
        agent_email = data.get('agent_email')

        if not agent_email:
            return jsonify({"error": "agent_email is required"}), 400

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("SELECT mls_number, address FROM properties WHERE id = %s", (property_id,))
        prop = cur.fetchone()
        if not prop:
            cur.close(); conn.close()
            return jsonify({"error": "Property not found"}), 404

        cur.execute("""
            SELECT DISTINCT a.gmail_attachment_id, a.gmail_message_id, a.filename,
                   a.mime_type, a.category
            FROM attachments a
            WHERE a.property_id = %s
              AND a.is_foil = TRUE
              AND a.gmail_attachment_id IS NOT NULL
              AND a.gmail_message_id IS NOT NULL
            ORDER BY a.filename
        """, (property_id,))

        foil_attachments = cur.fetchall()
        cur.close()
        conn.close()

        if not foil_attachments:
            return jsonify({"error": "No FOIL documents found for this property"}), 404

        service = get_gmail_service()

        forward = MIMEMultipart()
        forward['to'] = agent_email
        forward['subject'] = f"FOIL Document(s): {prop['address']} (MLS: {prop['mls_number'] or 'N/A'})"

        body = "\n".join([
            f"FOIL Document(s) for: {prop['address']}",
            f"MLS: {prop['mls_number'] or 'N/A'}",
            f"Total FOIL files: {len(foil_attachments)}",
            "",
            "Files attached:",
            *[f"  • {a['filename']}" for a in foil_attachments],
            "",
            "---",
            "Sent from Island Advantage Property Management System"
        ])
        forward.attach(MIMEText(body, 'plain'))

        attached_count = 0
        errors = []

        for att in foil_attachments:
            try:
                gmail_att = service.users().messages().attachments().get(
                    userId='me',
                    messageId=att['gmail_message_id'],
                    id=att['gmail_attachment_id']
                ).execute()
                file_data = base64.urlsafe_b64decode(gmail_att['data'])
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(file_data)
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f'attachment; filename="{att["filename"]}"')
                forward.attach(part)
                attached_count += 1
            except Exception as e:
                errors.append(f"Failed {att['filename']}: {str(e)}")

        if attached_count == 0:
            return jsonify({"error": "Could not retrieve FOIL documents from Gmail", "details": errors}), 500

        raw_message = base64.urlsafe_b64encode(forward.as_bytes()).decode()
        sent = service.users().messages().send(userId='me', body={'raw': raw_message}).execute()

        return jsonify({
            "success": True,
            "message_id": sent['id'],
            "property": prop['address'],
            "forwarded_to": agent_email,
            "attachments_sent": attached_count,
            "errors": errors if errors else None
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500

@app.route('/api/forward', methods=['POST'])
def forward_email():
    """Forward property email with all attachments (legacy endpoint)"""
    try:
        data = request.json
        property_id = data.get('property_id')
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT mls_number, address, gmail_message_id 
            FROM properties 
            WHERE id = %s
        """, (property_id,))
        
        result = cur.fetchone()
        if not result:
            return jsonify({"error": "Property not found"}), 404
        
        mls_number, address, gmail_message_id = result
        cur.close()
        conn.close()
        
        if not gmail_message_id:
            return jsonify({"error": "No email linked to this property"}), 400
        
        service = get_gmail_service()
        
        original = service.users().messages().get(
            userId='me',
            id=gmail_message_id,
            format='full'
        ).execute()
        
        headers = original['payload']['headers']
        original_subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Property Listing')
        original_from = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
        original_date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown')
        
        forward = MIMEMultipart()
        forward['to'] = 'islandadvantage.status@gmail.com'
        forward['subject'] = f"Fwd: {original_subject}"
        
        body_text = f"""---------- Forwarded message ---------
From: {original_from}
Date: {original_date}
Subject: {original_subject}

MLS: {mls_number}
Address: {address}
"""
        
        forward.attach(MIMEText(body_text, 'plain'))
        
        payload = original['payload']
        attachment_count = 0
        
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('filename'):
                    attachment_id = part['body'].get('attachmentId')
                    if attachment_id:
                        attachment = service.users().messages().attachments().get(
                            userId='me',
                            messageId=gmail_message_id,
                            id=attachment_id
                        ).execute()
                        
                        file_data = base64.urlsafe_b64decode(attachment['data'])
                        
                        mime_part = MIMEBase('application', 'octet-stream')
                        mime_part.set_payload(file_data)
                        encoders.encode_base64(mime_part)
                        mime_part.add_header(
                            'Content-Disposition',
                            f'attachment; filename="{part["filename"]}"'
                        )
                        forward.attach(mime_part)
                        attachment_count += 1
        
        raw_message = base64.urlsafe_b64encode(forward.as_bytes()).decode()
        sent = service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()
        
        return jsonify({
            "success": True,
            "message_id": sent['id'],
            "mls_number": mls_number,
            "attachments_forwarded": attachment_count,
            "forwarded_to": "islandadvantage.status@gmail.com"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/email-property-by-email-id', methods=['POST'])
def email_property_by_email_id():
    """Forward a specific email by its property_emails table ID"""
    try:
        data = request.json
        email_id = data.get('email_id')
        agent_email = data.get('agent_email')
        
        if not email_id or not agent_email:
            return jsonify({"error": "email_id and agent_email required"}), 400
        
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("""
            SELECT pe.gmail_message_id, pe.email_subject, p.mls_number, p.address
            FROM property_emails pe
            JOIN properties p ON pe.property_id = p.id
            WHERE pe.id = %s
        """, (email_id,))
        
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if not row:
            return jsonify({"error": "Email not found"}), 404
        
        service = get_gmail_service()
        
        original = service.users().messages().get(
            userId='me',
            id=row['gmail_message_id'],
            format='full'
        ).execute()
        
        headers = original['payload']['headers']
        original_subject = next((h['value'] for h in headers if h['name'] == 'Subject'), row['email_subject'])
        original_from = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
        original_date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown')
        
        forward = MIMEMultipart()
        forward['to'] = agent_email
        forward['subject'] = f"Fwd: {original_subject}"
        
        body_text = f"""---------- Forwarded message ---------
From: {original_from}
Date: {original_date}
Subject: {original_subject}

MLS: {row['mls_number'] or 'N/A'}
Address: {row['address']}
"""
        forward.attach(MIMEText(body_text, 'plain'))
        
        payload = original['payload']
        attachment_count = 0
        
        def attach_parts(parts):
            nonlocal attachment_count
            for part in parts:
                if part.get('filename'):
                    att_id = part['body'].get('attachmentId')
                    if att_id:
                        try:
                            att = service.users().messages().attachments().get(
                                userId='me',
                                messageId=row['gmail_message_id'],
                                id=att_id
                            ).execute()
                            file_data = base64.urlsafe_b64decode(att['data'])
                            mime_part = MIMEBase('application', 'octet-stream')
                            mime_part.set_payload(file_data)
                            encoders.encode_base64(mime_part)
                            mime_part.add_header('Content-Disposition',
                                f'attachment; filename="{part["filename"]}"')
                            forward.attach(mime_part)
                            attachment_count += 1
                        except Exception:
                            pass
                if 'parts' in part:
                    attach_parts(part['parts'])
        
        if 'parts' in payload:
            attach_parts(payload['parts'])
        
        raw_message = base64.urlsafe_b64encode(forward.as_bytes()).decode()
        sent = service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()
        
        return jsonify({
            "success": True,
            "message_id": sent['id'],
            "forwarded_to": agent_email,
            "attachments_forwarded": attachment_count
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/stats')
def stats():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("SELECT COUNT(*) as total FROM properties")
        total = cur.fetchone()['total']
        
        cur.execute("SELECT current_status, COUNT(*) as cnt FROM properties GROUP BY current_status ORDER BY cnt DESC")
        by_status = {row['current_status']: row['cnt'] for row in cur.fetchall()}
        
        cur.execute("SELECT COUNT(*) as cnt FROM attachments")
        total_attachments = cur.fetchone()['cnt']
        
        cur.execute("SELECT COUNT(*) as cnt FROM attachments WHERE is_foil = TRUE")
        foil_count = cur.fetchone()['cnt']
        
        cur.execute("SELECT COUNT(*) as cnt FROM property_emails")
        total_emails = cur.fetchone()['cnt']
        
        cur.close()
        conn.close()
        
        return jsonify({
            "total_properties": total,
            "by_status": by_status,
            "total_attachments": total_attachments,
            "foil_documents": foil_count,
            "total_emails_tracked": total_emails,
            "last_sync": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "total_properties": 0,
            "last_sync": None
        })

@app.route('/api/admin/set-photo-url', methods=['POST'])
def set_photo_url():
    try:
        data = request.get_json()
        property_id = data.get('property_id')
        photo_url = data.get('photo_url', '').strip()
        if not property_id:
            return jsonify({'error': 'property_id required'}), 400
        conn = get_db()
        cur = conn.cursor()
        cur.execute('UPDATE properties SET primary_photo_url = %s WHERE id = %s RETURNING address',
                    (photo_url if photo_url else None, property_id))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Property not found'}), 404
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'address': row[0], 'photo_url': photo_url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)


@app.route('/email-health')
def email_health():
    """Email Processing Health Dashboard"""
    return render_template('email_health.html')

@app.route('/api/email-health/stats')
def email_health_stats():
    """API endpoint for email processing statistics"""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Recent email processing (last 7 days)
        cur.execute("""
            SELECT 
                DATE(email_date) as date,
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE property_id IS NOT NULL) as matched,
                COUNT(*) FILTER (WHERE property_id IS NULL) as unmatched
            FROM email_processing_log
            WHERE email_date >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY DATE(email_date)
            ORDER BY date DESC
        """)
        daily_stats = cur.fetchall()
        
        # Unmatched important emails
        cur.execute("""
            SELECT email_id, email_subject, email_from, email_date, error_message
            FROM email_processing_log
            WHERE property_id IS NULL
            AND processing_status = 'success'
            AND email_date >= CURRENT_DATE - INTERVAL '30 days'
            AND (
                email_subject ILIKE '%highest%best%'
                OR email_subject ILIKE '%price reduction%'
                OR email_subject ILIKE '%status update%'
                OR email_subject ILIKE '%new list price%'
            )
            ORDER BY email_date DESC
            LIMIT 20
        """)
        unmatched_important = cur.fetchall()
        
        # Email types processed
        cur.execute("""
            SELECT 
                CASE 
                    WHEN email_subject ILIKE '%highest%best%' THEN 'Highest & Best'
                    WHEN email_subject ILIKE '%new list price%' THEN 'New List Price'
                    WHEN email_subject ILIKE '%price reduction%' THEN 'Price Reduction'
                    WHEN email_subject ILIKE '%status update%' THEN 'Status Update'
                    WHEN email_subject ILIKE '%back on market%' THEN 'Back on Market'
                    WHEN email_subject ILIKE '%under contract%' THEN 'Under Contract'
                    WHEN email_subject ILIKE '%sold%' THEN 'Sold'
                    ELSE 'Other'
                END as email_type,
                COUNT(*) as count,
                COUNT(*) FILTER (WHERE property_id IS NOT NULL) as matched,
                COUNT(*) FILTER (WHERE property_id IS NULL) as unmatched
            FROM email_processing_log
            WHERE email_date >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY email_type
            ORDER BY count DESC
        """)
        email_types = cur.fetchall()
        
        # Recently created properties
        cur.execute("""
            SELECT id, address, current_status, created_at, data_source
            FROM properties
            WHERE data_source = 'email'
            AND created_at >= CURRENT_DATE - INTERVAL '7 days'
            ORDER BY created_at DESC
            LIMIT 10
        """)
        recent_properties = cur.fetchall()
        
        # Processing errors
        cur.execute("""
            SELECT email_id, email_subject, email_from, email_date, error_message
            FROM email_processing_log
            WHERE processing_status = 'error'
            AND email_date >= CURRENT_DATE - INTERVAL '7 days'
            ORDER BY email_date DESC
            LIMIT 10
        """)
        errors = cur.fetchall()
        
        # Overall stats
        cur.execute("""
            SELECT 
                COUNT(*) as total_emails,
                COUNT(*) FILTER (WHERE property_id IS NOT NULL) as matched_emails,
                COUNT(*) FILTER (WHERE property_id IS NULL) as unmatched_emails,
                COUNT(*) FILTER (WHERE processing_status = 'error') as error_emails
            FROM email_processing_log
            WHERE email_date >= CURRENT_DATE - INTERVAL '30 days'
        """)
        overall_stats = cur.fetchone()
        
        # Properties created from emails
        cur.execute("""
            SELECT COUNT(*) as count
            FROM properties
            WHERE data_source = 'email'
            AND created_at >= CURRENT_DATE - INTERVAL '30 days'
        """)
        properties_from_email = cur.fetchone()['count']
        
        cur.close()
        conn.close()
        
        return jsonify({
            "daily_stats": [{
                "date": row['date'].isoformat(),
                "total": row['total'],
                "matched": row['matched'],
                "unmatched": row['unmatched'],
                "match_rate": (row['matched'] / row['total'] * 100) if row['total'] > 0 else 0
            } for row in daily_stats],
            "unmatched_important": [{
                "email_id": row['email_id'],
                "subject": row['email_subject'],
                "from": row['email_from'],
                "date": row['email_date'].isoformat() if row['email_date'] else None,
                "error": row['error_message']
            } for row in unmatched_important],
            "email_types": [{
                "type": row['email_type'],
                "count": row['count'],
                "matched": row['matched'],
                "unmatched": row['unmatched'],
                "match_rate": (row['matched'] / row['count'] * 100) if row['count'] > 0 else 0
            } for row in email_types],
            "recent_properties": [{
                "id": row['id'],
                "address": row['address'],
                "status": row['current_status'],
                "created_at": row['created_at'].isoformat() if row['created_at'] else None
            } for row in recent_properties],
            "errors": [{
                "email_id": row['email_id'],
                "subject": row['email_subject'],
                "from": row['email_from'],
                "date": row['email_date'].isoformat() if row['email_date'] else None,
                "error": row['error_message']
            } for row in errors],
            "overall": {
                "total_emails": overall_stats['total_emails'],
                "matched_emails": overall_stats['matched_emails'],
                "unmatched_emails": overall_stats['unmatched_emails'],
                "error_emails": overall_stats['error_emails'],
                "match_rate": (overall_stats['matched_emails'] / overall_stats['total_emails'] * 100) if overall_stats['total_emails'] > 0 else 0,
                "properties_created": properties_from_email
            }
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/email-health/last-scan')
def email_last_scan():
    """Get timestamp of last email scan"""
    try:
        # Check Redis for last scan time
        last_scan = r.get('email_monitor:last_scan')
        
        if last_scan:
            return jsonify({
                "last_scan": last_scan,
                "status": "available"
            })
        else:
            # Fall back to last email in database
            conn = get_db()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
            cur.execute("""
                SELECT MAX(processed_at) as last_scan
                FROM email_processing_log
            """)
            result = cur.fetchone()
            cur.close()
            conn.close()
            
            if result and result['last_scan']:
                return jsonify({
                    "last_scan": result['last_scan'].isoformat(),
                    "status": "from_database"
                })
            else:
                return jsonify({
                    "last_scan": None,
                    "status": "never"
                })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/email-health/trigger-scan', methods=['POST'])
def trigger_email_scan():
    """Manually trigger an email scan"""
    try:
        import subprocess
        import threading
        
        def run_scan():
            """Run email monitor script in background"""
            try:
                result = subprocess.run(
                    ['/opt/island-realty/venv/bin/python3', 
                     '/opt/island-realty/monitor_email_v4.py'],
                    cwd='/opt/island-realty',
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                # Store scan time in Redis
                from datetime import datetime
                scan_time = datetime.now().isoformat() + "Z" + "Z"
                r.setex('email_monitor:last_scan', 3600, scan_time)  # Expire after 1 hour
                
                # Store result
                if result.returncode == 0:
                    r.setex('email_monitor:last_result', 3600, 'success')
                else:
                    r.setex('email_monitor:last_result', 3600, f'error: {result.stderr[:200]}')
                    
            except subprocess.TimeoutExpired:
                r.setex('email_monitor:last_result', 3600, 'timeout: scan took too long')
            except Exception as e:
                r.setex('email_monitor:last_result', 3600, f'error: {str(e)}')
        
        # Start scan in background thread
        thread = threading.Thread(target=run_scan, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "message": "Email scan started in background",
            "status": "running"
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/email-health/scan-status')
def email_scan_status():
    """Check status of last manual scan"""
    try:
        last_result = r.get('email_monitor:last_result')
        last_scan = r.get('email_monitor:last_scan')
        
        return jsonify({
            "last_scan": last_scan,
            "last_result": last_result or "no recent scans",
            "status": "success" if last_result == "success" else "error" if last_result and 'error' in last_result else "unknown"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin')
def admin_dashboard():
    """Admin Dashboard - formerly Email Health"""
    return render_template('admin_dashboard.html')

@app.route('/api/admin/upload-act-spreadsheet', methods=['POST'])
def upload_act_spreadsheet():
    """Upload and process ACT spreadsheet PDF.
    
    This is the SINGLE SOURCE OF TRUTH upload. Any property not on the
    spreadsheet will be automatically deactivated (hidden from the site).
    """
    try:
        import tempfile
        import sys
        import os
        sys.path.insert(0, '/opt/island-realty/scripts')
        from act_reconciliation import reconcile_act_vs_database
        
        # Check if file uploaded
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        if not file.filename.endswith('.pdf'):
            return jsonify({"error": "File must be a PDF"}), 400
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name
        
        try:
            # Run reconciliation
            results = reconcile_act_vs_database(tmp_path)
            
            # --- APPLY CHANGES: Spreadsheet is single source of truth ---
            conn = get_db()
            cur = conn.cursor()
            
            # Spreadsheet NEVER deactivates properties - emails/manual actions control is_active
            matched_db_ids = [m["db_id"] for m in results.get("matched", []) if m.get("db_id")]
            reactivated = len(matched_db_ids)
            deactivated_count = 0
            
            
            # Step 4: Also update status/price for matched properties from spreadsheet
            for match in results.get('matched', []):
                if match.get('db_id') and match.get('reo_status'):
                    cur.execute(
                        """UPDATE properties 
                           SET reo_status = %s,
                               current_status = COALESCE(
                                   CASE WHEN email_date > '2026-01-27' THEN current_status ELSE %s END,
                                   %s
                               )
                           WHERE id = %s""",
                        (match['reo_status'], match['reo_status'], match['reo_status'], match['db_id'])
                    )
            
            conn.commit()
            cur.close()
            conn.close()
            
            # Store results in Redis for later retrieval
            import json
            results['applied'] = True
            results['reactivated'] = reactivated
            results['deactivated'] = deactivated_count
            redis_key = f'act_reconciliation:{datetime.now().strftime("%Y%m%d_%H%M%S")}'
            r.setex(redis_key, 86400, json.dumps(results, default=str))
            
            # Clean up temp file
            os.unlink(tmp_path)
            
            return jsonify({
                "success": True,
                "filename": file.filename,
                "results": {
                    "matched": len(results['matched']),
                    "reactivated": reactivated,
                    "deactivated": deactivated_count,
                    "in_act_not_db": len(results['in_act_not_db']),
                    "in_db_not_act": len(results['in_db_not_act']),
                    "timestamp": results['timestamp'],
                    "applied": True
                },
                "details": results,
                "redis_key": redis_key
            })
            
        except Exception as e:
            # Clean up temp file on error
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise e
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route('/api/admin/act-reconciliation-history')
def act_reconciliation_history():
    """Get history of ACT reconciliations"""
    try:
        # Get all reconciliation keys from Redis
        keys = []
        for key in r.scan_iter('act_reconciliation:*'):
            keys.append(key)
        
        history = []
        import json
        
        for key in sorted(keys, reverse=True)[:10]:  # Last 10
            data = r.get(key)
            if data:
                results = json.loads(data)
                history.append({
                    "timestamp": results.get('timestamp'),
                    "matched": len(results.get('matched', [])),
                    "in_act_not_db": len(results.get('in_act_not_db', [])),
                    "in_db_not_act": len(results.get('in_db_not_act', [])),
                    "key": key
                })
        
        return jsonify({"history": history})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Backward compatibility - redirect old email-health URL
@app.route('/email-health')
def email_health_redirect():
    from flask import redirect
    return redirect('/admin', code=301)


@app.route('/api/attachments/<int:attachment_id>/view')
def view_attachment(attachment_id):
    """Fetch an attachment from Gmail and serve it inline for viewing/printing"""
    from flask import Response
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cur.execute("""
            SELECT a.id, a.filename, a.mime_type, a.gmail_attachment_id, a.gmail_message_id,
                   a.category, a.is_foil
            FROM attachments a
            WHERE a.id = %s
        """, (attachment_id,))
        
        att = cur.fetchone()
        cur.close()
        conn.close()
        
        if not att:
            return jsonify({"error": "Attachment not found"}), 404
        
        if not att['gmail_attachment_id'] or not att['gmail_message_id']:
            return jsonify({"error": "Attachment not available in Gmail"}), 404
        
        # Fetch from Gmail
        service = get_gmail_service()
        gmail_att = service.users().messages().attachments().get(
            userId='me',
            messageId=att['gmail_message_id'],
            id=att['gmail_attachment_id']
        ).execute()
        
        file_data = base64.urlsafe_b64decode(gmail_att['data'])
        
        mime_type = att['mime_type'] or 'application/octet-stream'
        
        # Serve inline for PDFs only (PDF-only attachment policy)
        if mime_type == 'application/pdf':
            disposition = f'inline; filename="{att["filename"]}"'
        else:
            disposition = f'attachment; filename="{att["filename"]}"'
        
        return Response(
            file_data,
            mimetype=mime_type,
            headers={
                'Content-Disposition': disposition,
                'Content-Length': str(len(file_data)),
                'Cache-Control': 'private, max-age=300'
            }
        )
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route('/api/attachments/<int:attachment_id>/download')
def download_attachment(attachment_id):
    """Fetch an attachment from Gmail and force-download it as a PDF"""
    from flask import Response
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT a.id, a.filename, a.mime_type, a.gmail_attachment_id, a.gmail_message_id,
                   a.category, a.is_foil
            FROM attachments a
            WHERE a.id = %s
              AND (a.mime_type = 'application/pdf' OR a.filename ILIKE '%%.pdf')
        """, (attachment_id,))
        att = cur.fetchone()
        cur.close()
        conn.close()

        if not att:
            return jsonify({"error": "Attachment not found or not a PDF"}), 404
        if not att['gmail_attachment_id'] or not att['gmail_message_id']:
            return jsonify({"error": "Attachment not available in Gmail"}), 404

        service = get_gmail_service()
        gmail_att = service.users().messages().attachments().get(
            userId='me',
            messageId=att['gmail_message_id'],
            id=att['gmail_attachment_id']
        ).execute()

        file_data = base64.urlsafe_b64decode(gmail_att['data'])

        return Response(
            file_data,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="{att["filename"]}"',
                'Content-Length': str(len(file_data)),
                'Cache-Control': 'private, max-age=300'
            }
        )

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return jsonify({"error": str(e), "traceback": tb}), 500


@app.route('/api/admin/upload-spreadsheet', methods=['POST'])
def upload_spreadsheet():
    """Upload weekly Excel/CSV inventory spreadsheet.
    
    This is the SINGLE SOURCE OF TRUTH. Properties NOT on the spreadsheet
    are automatically deactivated (hidden from the site).
    
    Expected columns (flexible matching):
    - Address / Street / Property (required)
    - City (optional, used for disambiguation)
    - Status / REO Status / Current Status (optional)
    - Price / List Price (optional)
    """
    try:
        import tempfile, os, re, io
        import pandas as pd

        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400

        fname = file.filename.lower()
        if not (fname.endswith('.xlsx') or fname.endswith('.xls') or fname.endswith('.csv')):
            return jsonify({"error": "File must be Excel (.xlsx/.xls) or CSV"}), 400

        # Read into DataFrame
        file_bytes = file.read()
        if fname.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(file_bytes))
        else:
            df = pd.read_excel(io.BytesIO(file_bytes))

        df.columns = [str(c).strip() for c in df.columns]

        # --- Flexible column detection ---
        def find_col(df, candidates):
            for c in df.columns:
                if any(cand.lower() in c.lower() for cand in candidates):
                    return c
            return None

        addr_col   = find_col(df, ['address 1', 'address1', 'address', 'street', 'property', 'addr'])
        addr2_col  = find_col(df, ['address 2', 'address2', 'unit', 'suite', 'apt', 'apt #', 'unit #'])
        city_col   = find_col(df, ['city', 'town', 'municipality'])
        status_col = find_col(df, ['status', 'reo status', 'current status', 'listing status'])
        price_col  = find_col(df, ['price', 'list price', 'listing price', 'asking'])

        if not addr_col:
            return jsonify({"error": f"Could not find address column. Columns found: {list(df.columns)}"}), 400

        # Build list of spreadsheet addresses
        def normalize_addr(a):
            if not a:
                return ''
            a = str(a).lower().strip()
            a = re.sub(r'\s+', ' ', a)
            for old, new in [(' street',' st'),(' road',' rd'),(' avenue',' ave'),
                             (' boulevard',' blvd'),(' drive',' dr'),(' lane',' ln'),
                             (' court',' ct'),(' place',' pl')]:
                a = a.replace(old, new)
            a = a.replace('.', '')
            return a

        sheet_properties = []
        for _, row in df.iterrows():
            addr = str(row[addr_col]).strip() if pd.notna(row[addr_col]) else ''
            if not addr or addr.lower() in ('nan', 'none', ''):
                continue
            addr2 = str(row[addr2_col]).strip() if addr2_col and pd.notna(row[addr2_col]) else ''
            if addr2.lower() in ('nan', 'none', ''): addr2 = ''
            city = str(row[city_col]).strip() if city_col and pd.notna(row[city_col]) else ''
            full_addr = f"{addr}, {city}" if city else addr
            raw_status = str(row[status_col]).strip() if status_col and pd.notna(row[status_col]) else None
            # Normalize status variants to canonical form
            def normalize_status_val(s):
                if not s or s.lower() in ('nan', 'none', ''):
                    return None
                sl = s.lower().strip()
                if sl in ('pending', 'under contract', 'pended', 'in contract'):
                    return 'In Contract'
                if sl in ('available', 'lpp', 'auction/available', 'auction available'):
                    return 'Auction Available'
                if sl in ('1st accept', '1st accepted', 'first accepted'):
                    return 'First Accepted'
                if sl in ('t-o-t-m', 'totm', 'temporarily off the market'):
                    return 'TOTM'
                if sl in ('highest and best', 'highest & best'):
                    return 'Highest & Best'
                return s
            status = normalize_status_val(raw_status)
            price = None
            if price_col and pd.notna(row[price_col]):
                try:
                    price_str = str(row[price_col]).replace('$','').replace(',','').strip()
                    price = float(price_str)
                except:
                    pass
            sheet_properties.append({
                'address': addr,
                'address_2': addr2,
                'city': city,
                'full_address': full_addr,
                'normalized': normalize_addr(full_addr),
                'street_number': re.match(r'^(\d+)', addr.strip()).group(1) if re.match(r'^(\d+)', addr.strip()) else None,
                'status': status,
                'price': price
            })

        if not sheet_properties:
            return jsonify({"error": "No valid property rows found in spreadsheet"}), 400

        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get all DB properties
        cur.execute("SELECT id, address FROM properties")
        db_props = cur.fetchall()

        def normalize_addr_db(a):
            return normalize_addr(a or '')

        matched_ids = set()
        matched_count = 0
        updated_count = 0

        for sp in sheet_properties:
            sp_norm = sp['normalized']
            sp_num = sp['street_number']
            best_id = None
            best_score = 0

            for dbp in db_props:
                db_norm = normalize_addr_db(dbp['address'])
                db_num = re.match(r'^(\d+)', dbp['address'].strip()).group(1) if re.match(r'^(\d+)', dbp['address'].strip()) else None

                score = 0
                # Street number match is strongest signal
                if sp_num and db_num and sp_num == db_num:
                    score += 50
                # Partial address match
                if sp_norm and db_norm:
                    sp_words = set(sp_norm.split())
                    db_words = set(db_norm.split())
                    overlap = len(sp_words & db_words)
                    if overlap > 0:
                        score += overlap * 10

                if score > best_score and score >= 30:
                    best_score = score
                    best_id = dbp['id']

            if best_id:
                matched_ids.add(best_id)
                matched_count += 1
                # Spreadsheet is SINGLE SOURCE OF TRUTH: always overwrite status/price
                update_parts = []  # Never touch is_active - emails/manual actions control it
                update_vals = []
                if sp.get('address_2'):
                    update_parts.append("address_2 = %s")
                    update_vals.append(sp['address_2'])
                if sp.get('city'):
                    update_parts.append("city = %s")
                    update_vals.append(sp['city'])
                if sp['status']:
                    update_parts.append("current_status = %s")
                    update_vals.append(sp['status'])
                if sp['price']:
                    update_parts.append("current_list_price = %s")
                    update_vals.append(sp['price'])
                update_vals.append(best_id)
                if update_parts:  # Only execute if there's something to update
                    cur.execute(f"UPDATE properties SET {', '.join(update_parts)} WHERE id = %s", update_vals)
                    updated_count += 1

        # Deactivate properties NOT on spreadsheet (safe - address matching now works)
        if matched_ids:
            matched_list = list(matched_ids)
            cur.execute(
                "UPDATE properties SET is_active = FALSE WHERE id != ALL(%s) AND is_active = TRUE",
                (matched_list,)
            )
            deactivated_count = cur.rowcount
        else:
            deactivated_count = 0

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "filename": file.filename,
            "results": {
                "spreadsheet_rows": len(sheet_properties),
                "matched_in_db": matched_count,
                "updated": updated_count,
                "deactivated": deactivated_count,
                "message": f"✅ {matched_count} properties activated, {deactivated_count} deactivated (not on spreadsheet)"
            }
        })

    except ImportError:
        return jsonify({"error": "pandas/openpyxl not installed. Run: pip install pandas openpyxl --break-system-packages"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
