from google_auth_oauthlib.flow import InstalledAppFlow
import pickle

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify', 
    'https://www.googleapis.com/auth/gmail.send'
]

flow = InstalledAppFlow.from_client_secrets_file(
    '/opt/island-realty/config/gmail-credentials.json',
    SCOPES,
    redirect_uri='urn:ietf:wg:oauth:2.0:oob'  # Manual code flow
)

auth_url, _ = flow.authorization_url(prompt='consent')

print("\n" + "="*80)
print("VISIT THIS URL IN YOUR BROWSER:")
print("="*80)
print(auth_url)
print("="*80)
print("\nAfter authorizing, you'll see a CODE. Paste it here:")

code = input("Enter code: ").strip()

flow.fetch_token(code=code)

with open('/opt/island-realty/config/token.pickle', 'wb') as token:
    pickle.dump(flow.credentials, token)

print("\n✓ Success! Token saved with send permissions!")
