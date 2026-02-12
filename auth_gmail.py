from google_auth_oauthlib.flow import InstalledAppFlow
import pickle

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify', 
    'https://www.googleapis.com/auth/gmail.send'
]

print("\n🔐 Authenticating Gmail with SEND permissions...\n")

flow = InstalledAppFlow.from_client_secrets_file(
    '/opt/island-realty/config/gmail-credentials.json',
    SCOPES
)

# This will open a browser window - follow the prompts
creds = flow.run_local_server(port=8080, open_browser=False)

with open('/opt/island-realty/config/token.pickle', 'wb') as token:
    pickle.dump(creds, token)

print("\n✅ SUCCESS! Token saved with scopes:")
for scope in creds.scopes:
    print(f"   ✓ {scope}")
print("\n🎉 Gmail is ready to send emails!\n")
