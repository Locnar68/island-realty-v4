# Website Task Fixes - Implementation Summary

## Completed Fixes (2/9/2026)

### 1. ✅ Deduplication Script
**Problem**: Duplicate properties exist (e.g., "140-04 123rd Avenue Jamaica")

**Solution**: Created `/opt/island-realty/scripts/dedupe_properties.py`

**Features**:
- Finds duplicate properties by normalized address matching
- Intelligently chooses primary property (most attachments > has MLS > most recent)
- Merges all related data (attachments, emails, status history, alerts, flags)
- Adds database constraint to prevent future duplicates
- Safe: Shows preview and requires confirmation before executing

**Usage**:
```bash
cd /opt/island-realty
./scripts/dedupe_properties.py
```

**Current duplicates found**: 10 groups (including 140-04 123rd Avenue Jamaica)

---

### 2. ✅ Status Mapping: "New list price" → "Active"
**Problem**: "New list price" emails weren't being recognized as Available status

**Solution**: Updated `/opt/island-realty/app/email_processor.py`

**Changes**:
- Added "New list price" keyword detection
- Maps to "Active" status (indicating property is available)
- Works in both subject line detection and AI extraction

**Status mapping now includes**:
- New list price → Active
- Back on market → Active
- New listing → Active
- Price reduction → Price Reduced
- 1st accepted → First Accepted
- Under contract → In Contract
- Sold → Sold

---

### 3. ✅ Auto-Create Missing Properties
**Problem**: 140 Arlington Avenue, Valley Stream (2/5/2026 email) missing from database

**Solution**: Created `/opt/island-realty/scripts/import_missing_property.py`

**Features**:
- Searches Gmail for specific emails by date range and keywords
- Extracts property data using AI
- Creates property record automatically
- Links all attachments and emails
- Logs processing in email_processing_log

**Usage**:
```bash
cd /opt/island-realty
./scripts/import_missing_property.py
```

**Search strategy**:
1. Searches by subject ("new list price") + date range
2. Searches by address + date range
3. Searches by keywords + date range

---

### 4. ✅ FOIL Attachment Handling
**Status**: Already working correctly! 

**Current behavior**:
- ✅ `is_foil` flag properly set on attachments
- ✅ API endpoint `/api/properties/<id>/attachments` returns FOIL separately
- ✅ Attachments sorted with FOIL first
- ✅ FOIL count included in response

**API Response Structure**:
```json
{
  "property_id": 7,
  "total": 5,
  "foil_count": 2,
  "attachments": [
    {
      "id": 123,
      "filename": "892_Bay_9th_FOIL.pdf",
      "category": "FOIL",
      "is_foil": true,
      "gmail_attachment_id": "...",
      "gmail_message_id": "...",
      "source_email_date": "2023-01-23",
      "email_subject": "FOIL Documents"
    },
    ...
  ]
}
```

**Note**: If FOIL isn't displaying on frontend, the issue is in the React/web components, not the backend API.

---

### 5. ✅ Email Import Logging & Diagnostics
**Problem**: No way to troubleshoot why FOIL emails aren't processing correctly

**Solution**: Created `/opt/island-realty/scripts/email_import_log.py`

**Features**:
- View recent email processing (last 24 hours or custom)
- Show failed processing attempts with error messages
- Attachment statistics by category
- Detailed view for specific emails
- Shows: subject, status, property ID, actions taken, processing time

**Usage**:
```bash
# View recent emails
./scripts/email_import_log.py recent

# View last 48 hours
./scripts/email_import_log.py recent 48

# Show failed processing
./scripts/email_import_log.py failed

# Show attachment stats
./scripts/email_import_log.py attachments

# Show details for specific email
./scripts/email_import_log.py detail <id>
```

**Example output**:
```
EMAIL PROCESSING LOG - Last 24 hours
========================================

SUMMARY:
  Total emails processed: 15
  Successful: 12 (80.0%)
  Failed: 1
  No property data: 2

DETAILED LOG:
+----+-------------+----------------------------------+----------+---------+-------------------+--------+
| ID | Time        | Subject                          | Status   | Prop ID | Actions           | Time   |
+====+=============+==================================+==========+=========+===================+========+
| 45 | 02/09 14:30 | Status Update - 140-04 123rd Ave | success  | 7       | ["found_exist...] | 1245ms |
| 44 | 02/09 13:15 | New List Price - 892 Bay 9th St  | success  | 12      | ["property_cr...] | 1567ms |
+----+-------------+----------------------------------+----------+---------+-------------------+--------+
```

---

## How to Apply All Fixes

### Step 1: Run Deduplication
```bash
cd /opt/island-realty
./scripts/dedupe_properties.py
# Review duplicates, type "yes" to proceed
```

### Step 2: Import Missing Property
```bash
./scripts/import_missing_property.py
```

### Step 3: Restart Services (to pick up email_processor.py changes)
```bash
sudo systemctl restart island-realty
sudo systemctl restart island-email-monitor
```

### Step 4: Verify with Logs
```bash
# Check recent email processing
./scripts/email_import_log.py recent

# Check FOIL attachments
./scripts/email_import_log.py attachments

# Verify services running
sudo systemctl status island-realty island-email-monitor
```

---

## Database Changes

### New Constraints
- Unique index on `normalize_address(address)` in properties table
- Prevents future duplicate properties with same address (normalized)

### Existing Schema (verified working)
- `attachments.is_foil` boolean - properly set during email processing
- `attachments.category` - includes "FOIL" as valid category
- `email_processing_log` - tracks all email processing attempts

---

## Frontend Notes

### FOIL Display Issue
If FOIL documents aren't showing on the property card:

**API is working** - verified returning FOIL attachments with `is_foil: true`

**Check frontend**:
1. Is the React component calling `/api/properties/<id>/attachments`?
2. Is it filtering for `is_foil === true` to show FOIL section?
3. Is it displaying all attachments or just the first one?

**Expected frontend behavior**:
```jsx
// Property card should have:
<section className="foil-documents">
  <h3>FOIL Documents ({foilCount})</h3>
  {attachments.filter(a => a.is_foil).map(att => (
    <div key={att.id}>
      <a href={`/api/attachments/${att.id}/download`}>
        {att.filename}
      </a>
      <span>{att.source_email_date}</span>
    </div>
  ))}
</section>
```

---

## Verification Checklist

- [ ] Run deduplication script
- [ ] Verify "140-04 123rd Avenue Jamaica" now shows as single property
- [ ] Import missing "140 Arlington Avenue" property
- [ ] Restart both services
- [ ] Send test "New list price" email → verify status becomes "Active"
- [ ] Check email import log shows recent processing
- [ ] Verify FOIL attachments appear in API response
- [ ] Check frontend displays FOIL documents separately

---

## Ongoing Monitoring

### Daily Checks
```bash
# Check for new duplicates
psql -d island_properties -U island_user -c "SELECT address, COUNT(*) as count FROM properties GROUP BY address HAVING COUNT(*) > 1;"

# Check recent email processing
./scripts/email_import_log.py recent

# Check for failed emails
./scripts/email_import_log.py failed
```

### Weekly Checks
```bash
# Check attachment statistics
./scripts/email_import_log.py attachments

# Verify service health
sudo systemctl status island-realty island-email-monitor
```

---

## Support Commands

```bash
# View logs
tail -f /opt/island-realty/logs/email_monitor_v4.log

# Check database directly
psql -d island_properties -U island_user

# Restart services
sudo systemctl restart island-realty island-email-monitor

# Clear Python cache (if import issues)
find /opt/island-realty -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
```

---

## Files Modified/Created

### Modified
- `/opt/island-realty/app/email_processor.py` - Added "New list price" → "Active" mapping

### Created
- `/opt/island-realty/scripts/dedupe_properties.py` - Deduplication script
- `/opt/island-realty/scripts/import_missing_property.py` - Missing property import
- `/opt/island-realty/scripts/email_import_log.py` - Diagnostic logging tool

### Backups
- `/opt/island-realty/app/email_processor.py.backup` - Original file before changes

---

## Next Steps (Future Enhancements)

1. **Frontend FOIL Section**: Add dedicated FOIL documents display on property cards
2. **Batch Email Import**: Create tool to import multiple missing emails at once
3. **Automated Duplicate Detection**: Run nightly check for new duplicates
4. **Email Processing Dashboard**: Web-based view of email_import_log data
5. **Attachment Forwarding**: Implement FOIL document forwarding to Rob's email

