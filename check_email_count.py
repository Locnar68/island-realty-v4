#!/usr/bin/env python3
import pickle
from googleapiclient.discovery import build

TOKEN_FILE = '/opt/island-realty/config/token.pickle'

with open(TOKEN_FILE, 'rb') as token:
    creds = pickle.load(token)

service = build('gmail', 'v1', credentials=creds)

# Count ALL property emails
queries = [
    'from:@iarny.com',
    'subject:MLS',
    'subject:property',
    'subject:listing',
]

print("Checking email counts in Gmail...\n")

for query in queries:
    results = service.users().messages().list(userId='me', q=query).execute()
    count = results.get('resultSizeEstimate', 0)
    print(f"{query:30} = {count:5} emails")

# Total property-related
results = service.users().messages().list(
    userId='me', 
    q='subject:MLS OR subject:property OR subject:listing OR subject:BOM OR subject:price OR from:@iarny.com'
).execute()
total = results.get('resultSizeEstimate', 0)

print(f"\n{'TOTAL property emails':30} = {total:5} emails")
print(f"\nCurrently imported: 46 properties")
print(f"Need to import: {total - 46} more!\n")
