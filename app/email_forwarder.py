"""
Email Forwarder V2 - Forwards actual Gmail messages with attachments
FIXED: Recursive attachment detection
"""
import os
import pickle
from googleapiclient.discovery import build
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import base64

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send'
]

class EmailForwarder:
    def __init__(self):
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Authenticate with Gmail API"""
        creds = None
        token_path = '/opt/island-realty/config/token.pickle'

        if os.path.exists(token_path):
            with open(token_path, 'rb') as token:
                creds = pickle.load(token)

        if creds:
            self.service = build('gmail', 'v1', credentials=creds)

    def forward_property_email(self, property_data: dict, agent_email: str) -> bool:
        """
        Forward the actual Gmail email to agent with all attachments
        """
        try:
            gmail_message_id = property_data.get('gmail_message_id')

            if not gmail_message_id:
                # No original email - send notification
                return self._send_notification(property_data, agent_email)

            # Get the original message
            original = self.service.users().messages().get(
                userId='me',
                id=gmail_message_id,
                format='full'
            ).execute()

            # Get email metadata
            payload = original['payload']
            headers = payload.get('headers', [])

            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
            from_email = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown')

            # Create forward message
            message = MIMEMultipart()
            message['to'] = agent_email
            message['subject'] = f"Fwd: {subject}"

            # Add forward header with property info
            forward_header = f"""
<div style="border-left: 4px solid #1e40af; padding-left: 15px; margin: 20px 0; color: #666;">
<p><strong>---------- Forwarded Property Email ---------</strong></p>
<p><strong>From:</strong> {from_email}</p>
<p><strong>Subject:</strong> {subject}</p>
<p><strong>Property:</strong> {property_data.get('address', 'Unknown')}</p>
<p><strong>MLS:</strong> {property_data.get('mls_number', 'N/A')}</p>
<p><strong>Price:</strong> ${property_data.get('price', 0):,.0f}</p>
</div>
<hr style="border: 1px solid #ccc; margin: 20px 0;">
"""

            # Get email body
            body = self._get_email_body(payload)
            full_body = forward_header + body

            message.attach(MIMEText(full_body, 'html'))

            # FIXED: Recursively find and attach ALL attachments
            self._attach_all_files(message, gmail_message_id, payload)

            # Send
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            self.service.users().messages().send(
                userId='me',
                body={'raw': raw}
            ).execute()

            print(f"✓ Forwarded email for {property_data.get('address')} to {agent_email}")
            return True

        except Exception as e:
            print(f"Error forwarding email: {e}")
            return False

    def _get_email_body(self, payload):
        """Extract email body from payload"""
        def get_body_recursive(part):
            if 'parts' in part:
                for subpart in part['parts']:
                    result = get_body_recursive(subpart)
                    if result:
                        return result
            else:
                if part['mimeType'] == 'text/html':
                    data = part['body'].get('data', '')
                    if data:
                        return base64.urlsafe_b64decode(data).decode()
                elif part['mimeType'] == 'text/plain':
                    data = part['body'].get('data', '')
                    if data:
                        text = base64.urlsafe_b64decode(data).decode()
                        # Convert plain text to HTML
                        return text.replace('\n', '<br>')
            return None

        body = get_body_recursive(payload)
        return body if body else '<p>No email body available</p>'

    def _attach_all_files(self, message, message_id, payload):
        """
        FIXED: Recursively find and attach ALL files from Gmail message
        """
        def find_attachments(part):
            """Recursively find all parts with attachments"""
            attachments = []
            
            # Check if this part has a filename (it's an attachment)
            if part.get('filename'):
                attachments.append(part)
            
            # Recursively check sub-parts
            if 'parts' in part:
                for subpart in part['parts']:
                    attachments.extend(find_attachments(subpart))
            
            return attachments

        # Find all attachments recursively
        all_attachments = find_attachments(payload)
        
        print(f"  Found {len(all_attachments)} attachment(s)")
        
        # Attach each file
        for part in all_attachments:
            self._attach_file(message, message_id, part)

    def _attach_file(self, message, message_id, part):
        """Attach file from Gmail message"""
        try:
            filename = part.get('filename', 'attachment')

            if 'attachmentId' in part['body']:
                attachment = self.service.users().messages().attachments().get(
                    userId='me',
                    messageId=message_id,
                    id=part['body']['attachmentId']
                ).execute()

                file_data = base64.urlsafe_b64decode(attachment['data'])

                attach = MIMEBase('application', 'octet-stream')
                attach.set_payload(file_data)
                encoders.encode_base64(attach)
                attach.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                message.attach(attach)
                print(f"  ✓ Attached: {filename}")
            else:
                # Inline attachment (no attachmentId)
                data = part['body'].get('data')
                if data:
                    file_data = base64.urlsafe_b64decode(data)
                    attach = MIMEBase('application', 'octet-stream')
                    attach.set_payload(file_data)
                    encoders.encode_base64(attach)
                    attach.add_header('Content-Disposition', f'attachment; filename="{filename}"')
                    message.attach(attach)
                    print(f"  ✓ Attached (inline): {filename}")
        except Exception as e:
            print(f"  ✗ Error attaching {part.get('filename')}: {e}")

    def _send_notification(self, property_data, agent_email):
        """Send simple notification if no original email"""
        try:
            html_body = f"""
<html>
<body style="font-family: Arial, sans-serif;">
<h2 style="color: #1e40af;">Property Information</h2>
<table style="border-collapse: collapse; width: 100%;">
<tr><td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>Address:</strong></td>
    <td style="padding: 8px; border-bottom: 1px solid #ddd;">{property_data.get('address')}</td></tr>
<tr><td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>MLS:</strong></td>
    <td style="padding: 8px; border-bottom: 1px solid #ddd;">{property_data.get('mls_number')}</td></tr>
<tr><td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>Price:</strong></td>
    <td style="padding: 8px; border-bottom: 1px solid #ddd;">${property_data.get('price', 0):,.0f}</td></tr>
<tr><td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>Status:</strong></td>
    <td style="padding: 8px; border-bottom: 1px solid #ddd;">{property_data.get('status')}</td></tr>
</table>
<p style="color: #666; margin-top: 20px;"><em>No original email available for this property.</em></p>
</body>
</html>
"""

            message = MIMEText(html_body, 'html')
            message['to'] = agent_email
            message['subject'] = f"Property: {property_data.get('address')}"

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            self.service.users().messages().send(
                userId='me',
                body={'raw': raw}
            ).execute()

            print(f"✓ Sent notification for {property_data.get('address')} to {agent_email}")
            return True
        except Exception as e:
            print(f"Error sending notification: {e}")
            return False
