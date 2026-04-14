import os
"""
Database Models for Island Advantage Property System V4
Cleaned up - removed duplicate PropertyEmail classes
"""

from datetime import datetime
from typing import Optional, List, Dict
import psycopg2
import psycopg2.extras
import json
from contextlib import contextmanager


@contextmanager
def db_connection():
    """Database connection context manager"""
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME', 'island_properties'),
            user=os.getenv('DB_USER', 'island_user'),
            password=os.getenv('DB_PASSWORD', 'Pepmi@12'),
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432')
        )
        yield conn
    finally:
        if conn:
            conn.close()


class EmailProcessingLog:
    """Email processing log"""
    
    @staticmethod
    def is_processed(email_id):
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM email_processing_log WHERE email_id = %s AND processing_status = 'success'", (email_id,))
            return cursor.fetchone() is not None
    
    @staticmethod
    def log(email_id, email_subject, email_from, email_date, status, property_id=None, 
            actions_taken=None, error_message=None, processing_time_ms=None, ai_model_used=None):
        import json
        
        # Convert actions_taken to JSON string if it's a list
        if isinstance(actions_taken, (list, dict)):
            actions_taken = json.dumps(actions_taken)
        
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO email_processing_log 
                (email_id, email_subject, email_from, email_date, processing_status,
                 property_id, actions_taken, error_message, processing_time_ms, ai_model_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (email_id) DO UPDATE SET
                    processing_status = EXCLUDED.processing_status,
                    processed_at = NOW()
            """, (email_id, email_subject, email_from, email_date, status,
                  property_id, actions_taken, error_message, processing_time_ms, ai_model_used))
            conn.commit()


class Database:
    """Database connection and query management"""
    
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.conn = None
    
    def connect(self):
        self.conn = psycopg2.connect(self.connection_string)
        self.conn.autocommit = False
        return self.conn
    
    def close(self):
        if self.conn:
            self.conn.close()
    
    def execute(self, query: str, params: tuple = None, fetch=True):
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cursor.execute(query, params)
            if fetch and cursor.description:
                return cursor.fetchall()
            self.conn.commit()
            return cursor.rowcount
        except Exception as e:
            self.conn.rollback()
            raise e
        finally:
            cursor.close()


class Property:
    """Property master record"""
    
    @staticmethod
    def create(db: Database, data: Dict) -> int:
        query = """
            INSERT INTO properties (
                mls_number, temporary_id, address, city, zip_code,
                property_type, current_list_price, original_list_price,
                assigned_agent_id, current_status, primary_photo_url,
                photo_gallery_json, data_source, last_email_id
            ) VALUES (
                %(mls_number)s, %(temporary_id)s, %(address)s, %(city)s, %(zip_code)s,
                %(property_type)s, %(current_list_price)s, %(original_list_price)s,
                %(assigned_agent_id)s, %(current_status)s, %(primary_photo_url)s,
                %(photo_gallery_json)s, %(data_source)s, %(last_email_id)s
            )
            ON CONFLICT (mls_number) DO UPDATE SET
                address = EXCLUDED.address,
                city = EXCLUDED.city,
                zip_code = EXCLUDED.zip_code,
                property_type = EXCLUDED.property_type,
                updated_at = NOW()
            RETURNING id
        """
        result = db.execute(query, data, fetch=True)
        return result[0]['id'] if result else None
    
    @staticmethod
    def find_by_mls(db: Database, mls_number: str) -> Optional[Dict]:
        query = """
            SELECT p.*, a.name as agent_name, a.email as agent_email
            FROM properties p
            LEFT JOIN agents a ON p.assigned_agent_id = a.id
            WHERE p.mls_number = %s
        """
        result = db.execute(query, (mls_number,))
        return result[0] if result else None
    
    @staticmethod
    def find_by_address(db: Database, address: str) -> Optional[Dict]:
        query = """
            SELECT p.*, a.name as agent_name
            FROM properties p
            LEFT JOIN agents a ON p.assigned_agent_id = a.id
            WHERE LOWER(p.address) LIKE LOWER(%s)
            LIMIT 1
        """
        result = db.execute(query, (f'%{address}%',))
        return result[0] if result else None
    
    @staticmethod
    def get_all(db: Database, filters: Dict = None) -> List[Dict]:
        where_clauses = []
        params = []
        
        if filters:
            if 'status' in filters:
                where_clauses.append("p.current_status = %s")
                params.append(filters['status'])
            if 'city' in filters:
                where_clauses.append("p.city = %s")
                params.append(filters['city'])
            if 'min_price' in filters:
                where_clauses.append("p.current_list_price >= %s")
                params.append(filters['min_price'])
            if 'max_price' in filters:
                where_clauses.append("p.current_list_price <= %s")
                params.append(filters['max_price'])
        
        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        
        query = f"""
            SELECT 
                p.*,
                a.name as agent_name,
                a.email as agent_email,
                (SELECT COUNT(*) FROM compliance_alerts ca 
                 WHERE ca.property_id = p.id AND ca.is_active = TRUE) as alert_count
            FROM properties p
            LEFT JOIN agents a ON p.assigned_agent_id = a.id
            {where_sql}
            ORDER BY p.updated_at DESC
        """
        
        return db.execute(query, tuple(params))
    
    @staticmethod
    def update_status(db: Database, property_id: int, new_status: str, 
                     source_email_id: str, source_email_subject: str = None,
                     source_email_date: datetime = None, changed_by: str = None) -> bool:
        current = db.execute(
            "SELECT current_status FROM properties WHERE id = %s",
            (property_id,)
        )
        if not current:
            return False
        
        old_status = current[0]['current_status']
        
        db.execute(
            """UPDATE properties SET current_status = %s, updated_at = NOW(), last_email_id = %s WHERE id = %s""",
            (new_status, source_email_id, property_id),
            fetch=False
        )
        
        StatusHistory.create(db, {
            'property_id': property_id,
            'old_status': old_status,
            'new_status': new_status,
            'source_email_id': source_email_id,
            'source_email_subject': source_email_subject,
            'source_email_date': source_email_date,
            'changed_by': changed_by
        })
        
        AuditLog.log(db, {
            'table_name': 'properties',
            'record_id': property_id,
            'action': 'STATUS_CHANGE',
            'old_values': json.dumps({'status': old_status}),
            'new_values': json.dumps({'status': new_status}),
            'source_email_id': source_email_id,
            'triggered_by': changed_by or 'EMAIL_SYSTEM'
        })
        
        db.conn.commit()
        return True


class StatusHistory:
    @staticmethod
    def create(db: Database, data: Dict) -> int:
        query = """
            INSERT INTO status_history (
                property_id, old_status, new_status, source_email_id,
                source_email_subject, source_email_date, changed_by, notes
            ) VALUES (
                %(property_id)s, %(old_status)s, %(new_status)s, %(source_email_id)s,
                %(source_email_subject)s, %(source_email_date)s, %(changed_by)s, %(notes)s
            )
            RETURNING id
        """
        result = db.execute(query, data, fetch=True)
        return result[0]['id'] if result else None
    
    @staticmethod
    def get_for_property(db: Database, property_id: int) -> List[Dict]:
        query = "SELECT * FROM status_history WHERE property_id = %s ORDER BY changed_at DESC"
        return db.execute(query, (property_id,))


class PropertyFlags:
    @staticmethod
    def create_or_update(db: Database, property_id: int, flags: Dict, source_email_id: str = None) -> bool:
        existing = db.execute("SELECT id, locked_at FROM property_flags WHERE property_id = %s", (property_id,))
        if existing and existing[0]['locked_at']:
            return False
        
        if existing:
            set_clauses = []
            params = []
            for key, value in flags.items():
                set_clauses.append(f"{key} = %s")
                params.append(value)
            if source_email_id:
                set_clauses.append("source_email_id = %s")
                params.append(source_email_id)
            set_clauses.append("updated_at = NOW()")
            params.append(property_id)
            query = f"UPDATE property_flags SET {', '.join(set_clauses)} WHERE property_id = %s"
            db.execute(query, tuple(params), fetch=False)
        else:
            flags['property_id'] = property_id
            flags['source_email_id'] = source_email_id
            columns = ', '.join(flags.keys())
            placeholders = ', '.join([f'%({k})s' for k in flags.keys()])
            query = f"INSERT INTO property_flags ({columns}) VALUES ({placeholders})"
            db.execute(query, flags, fetch=False)
        
        db.conn.commit()
        return True
    
    @staticmethod
    def get_for_property(db: Database, property_id: int) -> Optional[Dict]:
        result = db.execute("SELECT * FROM property_flags WHERE property_id = %s", (property_id,))
        return result[0] if result else None


class HighestBestDeadline:
    @staticmethod
    def create(db: Database, property_id: int, due_date: str, due_time: str,
               offer_rules: str = None, submission_instructions: str = None,
               source_email_id: str = None) -> int:
        db.execute(
            "UPDATE highest_best_deadlines SET is_active = FALSE, expired_at = NOW() WHERE property_id = %s AND is_active = TRUE",
            (property_id,), fetch=False
        )
        query = """
            INSERT INTO highest_best_deadlines (property_id, due_date, due_time, offer_rules, submission_instructions, source_email_id)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """
        result = db.execute(query, (property_id, due_date, due_time, offer_rules, submission_instructions, source_email_id), fetch=True)
        db.conn.commit()
        return result[0]['id'] if result else None


class ImportantPropertyInfo:
    @staticmethod
    def create(db: Database, property_id: int, category: str, title: str,
               content: str, severity: str = 'info', source_email_id: str = None,
               source_email_subject: str = None) -> int:
        query = """
            INSERT INTO important_property_info (property_id, category, title, content, severity, source_email_id, source_email_subject)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """
        result = db.execute(query, (property_id, category, title, content, severity, source_email_id, source_email_subject), fetch=True)
        db.conn.commit()
        return result[0]['id'] if result else None


class Attachment:
    @staticmethod
    def create(db: Database, data: Dict) -> int:
        query = """
            INSERT INTO attachments (
                property_id, filename, file_path, file_url, file_size,
                mime_type, category, subcategory, priority,
                source_email_id, source_email_date, notes, uploaded_by,
                gmail_attachment_id, gmail_message_id, is_foil
            ) VALUES (
                %(property_id)s, %(filename)s, %(file_path)s, %(file_url)s, %(file_size)s,
                %(mime_type)s, %(category)s, %(subcategory)s, %(priority)s,
                %(source_email_id)s, %(source_email_date)s, %(notes)s, %(uploaded_by)s,
                %(gmail_attachment_id)s, %(gmail_message_id)s, %(is_foil)s
            )
            RETURNING id
        """
        result = db.execute(query, data, fetch=True)
        db.conn.commit()
        return result[0]['id'] if result else None
    
    @staticmethod
    def get_for_property(db: Database, property_id: int, category: str = None) -> List[Dict]:
        if category:
            query = "SELECT * FROM attachments WHERE property_id = %s AND category = %s ORDER BY priority DESC, uploaded_at DESC"
            return db.execute(query, (property_id, category))
        else:
            query = "SELECT * FROM attachments WHERE property_id = %s ORDER BY priority DESC, uploaded_at DESC"
            return db.execute(query, (property_id,))


class ComplianceAlert:
    @staticmethod
    def create(db: Database, property_id: int, alert_type: str, title: str,
               description: str = None, severity: str = 'high',
               source_email_id: str = None, source_attachment_id: int = None) -> int:
        query = """
            INSERT INTO compliance_alerts (property_id, alert_type, title, description, severity, source_email_id, source_attachment_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """
        result = db.execute(query, (property_id, alert_type, title, description, severity, source_email_id, source_attachment_id), fetch=True)
        db.conn.commit()
        return result[0]['id'] if result else None
    
    @staticmethod
    def resolve(db: Database, alert_id: int, resolution_notes: str = None) -> bool:
        db.execute(
            "UPDATE compliance_alerts SET is_active = FALSE, resolved_at = NOW(), resolution_notes = %s WHERE id = %s",
            (resolution_notes, alert_id), fetch=False
        )
        db.conn.commit()
        return True


class AuditLog:
    @staticmethod
    def log(db: Database, data: Dict) -> int:
        query = """
            INSERT INTO audit_log (table_name, record_id, action, old_values, new_values, source_email_id, triggered_by)
            VALUES (%(table_name)s, %(record_id)s, %(action)s, %(old_values)s::jsonb, %(new_values)s::jsonb, %(source_email_id)s, %(triggered_by)s)
            RETURNING id
        """
        result = db.execute(query, data, fetch=True)
        return result[0]['id'] if result else None

