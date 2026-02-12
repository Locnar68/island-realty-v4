#!/usr/bin/env python3
"""
Island Advantage Property Management System V4
Attachment Manager - Download and organize email attachments
"""

import os
import base64
import logging
from pathlib import Path
from datetime import datetime
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

class AttachmentManager:
    """Manage email attachment downloads and organization"""
    
    def __init__(self, gmail_service, base_path='/opt/island-realty/attachments'):
        self.service = gmail_service
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        
        # Create category subdirectories
        self.categories = [
            'CO_TCO', 'Violations', 'ECB', 'Fines', 'FOIL',
            'Condition', 'Offers', 'Contracts', 'Closing', 'Other'
        ]
        
        for category in self.categories:
            (self.base_path / category).mkdir(exist_ok=True)
    
    def download_attachment(self, message_id, attachment_id, filename, category='Other'):
        """Download a single attachment"""
        try:
            # Get attachment data
            attachment = self.service.users().messages().attachments().get(
                userId='me',
                messageId=message_id,
                id=attachment_id
            ).execute()
            
            # Decode attachment data
            file_data = base64.urlsafe_b64decode(attachment['data'])
            
            # Generate safe filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            safe_filename = self._sanitize_filename(filename)
            final_filename = f"{timestamp}_{safe_filename}"
            
            # Determine category path
            category_path = self.base_path / category
            file_path = category_path / final_filename
            
            # Write file
            with open(file_path, 'wb') as f:
                f.write(file_data)
            
            logger.info(f"Downloaded attachment: {final_filename} to {category}")
            
            return {
                'success': True,
                'file_path': str(file_path),
                'filename': final_filename,
                'size': len(file_data)
            }
            
        except Exception as e:
            logger.error(f"Error downloading attachment {filename}: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def download_all_attachments(self, message_id, attachments_info):
        """Download all attachments from an email"""
        results = []
        
        for attachment_info in attachments_info:
            if 'attachmentId' in attachment_info:
                # Determine category from filename
                category = self._categorize_attachment(attachment_info['filename'])
                
                result = self.download_attachment(
                    message_id,
                    attachment_info['attachmentId'],
                    attachment_info['filename'],
                    category
                )
                
                results.append({
                    **result,
                    'original_filename': attachment_info['filename'],
                    'category': category
                })
        
        return results
    
    def _categorize_attachment(self, filename):
        """Categorize attachment based on filename"""
        filename_lower = filename.lower()
        
        if any(x in filename_lower for x in ['co ', 'c of o', 'c.o.', 'tco', 'certificate of occupancy']):
            return 'CO_TCO'
        elif any(x in filename_lower for x in ['violation', 'viol', 'hpd', 'dob']):
            return 'Violations'
        elif 'ecb' in filename_lower:
            return 'ECB'
        elif any(x in filename_lower for x in ['fine', 'penalty', 'lien']):
            return 'Fines'
        elif 'foil' in filename_lower:
            return 'FOIL'
        elif any(x in filename_lower for x in ['condition', 'report', 'inspection']):
            return 'Condition'
        elif any(x in filename_lower for x in ['offer', 'bid']):
            return 'Offers'
        elif any(x in filename_lower for x in ['contract', 'agreement', 'rider']):
            return 'Contracts'
        elif any(x in filename_lower for x in ['closing', 'hud', 'settlement']):
            return 'Closing'
        else:
            return 'Other'
    
    def _sanitize_filename(self, filename):
        """Sanitize filename for safe storage"""
        # Remove or replace unsafe characters
        unsafe_chars = '<>:"/\\|?*'
        for char in unsafe_chars:
            filename = filename.replace(char, '_')
        
        # Limit length
        name, ext = os.path.splitext(filename)
        if len(name) > 100:
            name = name[:100]
        
        return name + ext
    
    def get_attachment_url(self, file_path):
        """Generate URL for attachment (for future web access)"""
        # Convert absolute path to relative URL
        rel_path = Path(file_path).relative_to(self.base_path)
        return f"/attachments/{rel_path}"

