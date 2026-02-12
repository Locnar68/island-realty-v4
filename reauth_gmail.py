import pickle
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify', 
    'https://www.googleapis.com/auth/gmail.send'
]

def authenticate():
    creds = None
    
    # Start OAuth flow
    flow = InstalledAppFlow.from_client_secrets_file(
        '/opt/island-realty/config/gmail-credentials.json', 
        SCOPES
    )
    
    # This will print a URL - you'll need to visit it
    creds = flow.run_local_server(port=8080, open_browser=False)
    
    # Save the credentials
    with open('/opt/island-realty/config/token.pickle', 'wb') as token:
        pickle.dump(creds, token)
    
    print("✓ Authentication successful!")
    print(f"✓ Token saved with scopes: {creds.scopes}")

if __name__ == '__main__':
    authenticate()
