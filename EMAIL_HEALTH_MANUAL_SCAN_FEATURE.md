# Email Health Dashboard - Manual Scan Feature

## Date: February 10, 2026
## Status: ✅ **DEPLOYED & LIVE**

---

## 🎯 **NEW FEATURES ADDED**

### **1. Last Scan Timestamp**
**Location:** Top of Email Health Dashboard header

**What it shows:**
- Exact time of last email scan/injection
- Relative time (e.g., "5 min ago", "Just now")
- Updates automatically every minute

**How it works:**
- Checks Redis for recent manual scans
- Falls back to database `email_processing_log` table
- Shows "Never" if no scans found

---

### **2. Manual Scan Button**
**Location:** Next to timestamp in dashboard header

**Button States:**
- 🟢 **Ready:** "▶️ Run Email Scan" (green button)
- 🟡 **Scanning:** "⏳ Scanning..." (yellow, pulsing animation)
- ⚫ **Disabled:** Grayed out during scan

**What it does:**
1. Triggers `/opt/island-realty/monitor_email_v4.py` script
2. Runs in background (doesn't block page)
3. Polls for completion every 5 seconds
4. Auto-refreshes data when complete
5. Shows status messages

---

## 🔧 **TECHNICAL IMPLEMENTATION**

### **New API Endpoints:**

#### **1. GET `/api/email-health/last-scan`**
Returns timestamp of last email scan.

**Response:**
```json
{
  "last_scan": "2026-02-10T14:41:24.743877",
  "status": "from_database"
}
```

**Status values:**
- `"available"` - Recent scan time from Redis
- `"from_database"` - Fallback to last processed email
- `"never"` - No scans found

---

#### **2. POST `/api/email-health/trigger-scan`**
Manually triggers an email scan.

**Request:**
```bash
curl -X POST http://localhost:5000/api/email-health/trigger-scan
```

**Response:**
```json
{
  "success": true,
  "message": "Email scan started in background",
  "status": "running"
}
```

**How it works:**
- Spawns background thread
- Runs `monitor_email_v4.py` script
- Stores result in Redis (expires after 1 hour)
- Returns immediately (non-blocking)
- 60-second timeout for safety

---

#### **3. GET `/api/email-health/scan-status`**
Checks status of last manual scan.

**Response:**
```json
{
  "last_scan": "2026-02-10T14:54:00.123456",
  "last_result": "success",
  "status": "success"
}
```

**Result values:**
- `"success"` - Scan completed successfully
- `"error: ..."` - Scan failed with error message
- `"timeout: ..."` - Scan exceeded 60 seconds
- `"no recent scans"` - No manual scans run

---

## 🎨 **USER INTERFACE**

### **Scan Info Section:**
```
┌─────────────────────────────────────────────────┐
│ Last Scan: 5 min ago  [▶️ Run Email Scan]      │
└─────────────────────────────────────────────────┘
```

### **During Scan:**
```
┌─────────────────────────────────────────────────┐
│ Last Scan: 5 min ago  [⏳ Scanning...] (pulsing)│
│ ⚠️  Email scan in progress... This may take up   │
│     to 60 seconds.                               │
└─────────────────────────────────────────────────┘
```

### **After Success:**
```
┌─────────────────────────────────────────────────┐
│ Last Scan: Just now  [▶️ Run Email Scan]        │
│ ✓ Scan completed successfully! Refreshing data...│
└─────────────────────────────────────────────────┘
```

### **After Error:**
```
┌─────────────────────────────────────────────────┐
│ Last Scan: 5 min ago  [▶️ Run Email Scan]       │
│ ✗ Scan failed: error message here               │
└─────────────────────────────────────────────────┘
```

---

## 📊 **WORKFLOW**

### **Normal Email Processing:**
1. Email monitor service runs every 5 minutes (automatic)
2. Processes new emails from Gmail
3. Updates `email_processing_log` table
4. Dashboard shows "Last Scan: X min ago"

### **Manual Scan Process:**
1. User clicks "▶️ Run Email Scan" button
2. Button changes to "⏳ Scanning..." (disabled)
3. Status message: "Email scan in progress..."
4. Script runs in background for up to 60 seconds
5. JavaScript polls status every 5 seconds
6. When complete:
   - Button re-enables
   - Status: "✓ Scan completed successfully!"
   - Dashboard data refreshes automatically
   - Status message clears after 5 seconds
   - Timestamp updates to "Just now"

---

## 🚀 **USE CASES**

### **When to Use Manual Scan:**

1. **Just forwarded emails to system**
   - Want immediate processing
   - Don't want to wait 5 minutes for auto-scan

2. **Testing email processor changes**
   - Verify new emails are recognized
   - Check if matching works correctly

3. **After fixing unmatched emails**
   - Re-process emails that were previously missed
   - Verify fixes worked

4. **Troubleshooting**
   - Force a scan to see if emails are coming in
   - Check if Gmail connection is working
   - Debug processing issues

---

## ⚙️ **CONFIGURATION**

### **Redis Storage:**
```
Key: email_monitor:last_scan
Value: ISO timestamp
Expiry: 3600 seconds (1 hour)

Key: email_monitor:last_result
Value: "success" | "error: ..." | "timeout: ..."
Expiry: 3600 seconds (1 hour)
```

### **Script Location:**
```
/opt/island-realty/monitor_email_v4.py
```

### **Timeout:**
```python
timeout=60  # 60 seconds max
```

### **Poll Interval:**
```javascript
5000  // Check status every 5 seconds
```

---

## 🔍 **DEBUGGING**

### **Check if scan is running:**
```bash
ps aux | grep monitor_email_v4.py
```

### **View Redis scan time:**
```bash
redis-cli GET email_monitor:last_scan
redis-cli GET email_monitor:last_result
```

### **Manual scan from command line:**
```bash
cd /opt/island-realty
source venv/bin/activate
python3 monitor_email_v4.py
```

### **Check API responses:**
```bash
# Last scan time
curl http://localhost:5000/api/email-health/last-scan

# Trigger scan
curl -X POST http://localhost:5000/api/email-health/trigger-scan

# Check status
curl http://localhost:5000/api/email-health/scan-status
```

---

## ⚠️ **IMPORTANT NOTES**

### **Limitations:**
1. **One scan at a time**
   - Button disabled during scan
   - Cannot run multiple concurrent scans

2. **60-second timeout**
   - Scan automatically stops after 60 seconds
   - Prevents hung processes

3. **Background execution**
   - Scan runs independently
   - Page doesn't freeze
   - Results appear when ready

### **Best Practices:**
1. **Don't spam the button**
   - Wait for scan to complete
   - Each scan processes ~5 min of emails

2. **Check timestamp first**
   - If "Just now", don't need to scan again
   - Auto-scan runs every 5 minutes anyway

3. **Use for testing**
   - Manual scan = immediate feedback
   - Good for development/debugging
   - Not needed for normal operations

---

## 📁 **FILES MODIFIED**

### **Backend:**
```
/opt/island-realty/app/__init__.py
  - Added: /api/email-health/last-scan
  - Added: /api/email-health/trigger-scan
  - Added: /api/email-health/scan-status
```

### **Frontend:**
```
/opt/island-realty/app/templates/email_health.html
  - Added: Last scan timestamp display
  - Added: Manual scan button
  - Added: Scan status messages
  - Added: JavaScript polling logic
  - Added: Auto-refresh on completion
```

---

## ✅ **VERIFICATION**

### **Test Last Scan Endpoint:**
```bash
curl http://localhost:5000/api/email-health/last-scan
# Expected: JSON with timestamp and status
```

### **Test Trigger Scan:**
```bash
curl -X POST http://localhost:5000/api/email-health/trigger-scan
# Expected: {"success": true, ...}
```

### **Test UI:**
1. Visit: `http://your-server:5000/email-health`
2. Look for "Last Scan: X min ago" in header
3. Click "▶️ Run Email Scan" button
4. Verify button changes to "⏳ Scanning..."
5. Wait 5-60 seconds
6. Verify button returns to normal
7. Check timestamp updates to "Just now"

---

## 🎯 **SUCCESS METRICS**

**Feature is working when:**
- ✅ Last scan timestamp displays correctly
- ✅ Timestamp updates every minute
- ✅ Manual scan button is visible
- ✅ Clicking button triggers scan
- ✅ Button disables during scan
- ✅ Status messages appear
- ✅ Data refreshes after scan
- ✅ No errors in console
- ✅ Multiple scans can be run sequentially

---

## 📞 **SUPPORT**

### **Common Issues:**

**"Scan button does nothing"**
- Check browser console for errors
- Verify `/api/email-health/trigger-scan` endpoint works
- Check island-realty service is running

**"Timestamp shows 'Unknown'"**
- Check Redis is running: `redis-cli ping`
- Check database connection
- Verify email_processing_log table has data

**"Scan never completes"**
- Check if script is hung: `ps aux | grep monitor_email`
- Check Redis for result: `redis-cli GET email_monitor:last_result`
- Kill hung process: `pkill -f monitor_email_v4.py`

---

## 🎉 **SUMMARY**

**What You Asked For:**
"Add a time stamp when the last injection happened to the email-health.. create a button that allows me to do manual scan injection"

**What You Got:**
- ✅ Last scan timestamp (auto-updating)
- ✅ Manual scan button with status
- ✅ Background processing (non-blocking)
- ✅ Status messages (success/error/running)
- ✅ Auto-refresh after scan
- ✅ Visual feedback (colors, animations)
- ✅ 3 new API endpoints
- ✅ Full error handling
- ✅ Redis caching for performance

**The dashboard now has complete manual control over email scanning with full visibility into when scans happen!**

---

**END OF MANUAL SCAN FEATURE DOCUMENTATION**

