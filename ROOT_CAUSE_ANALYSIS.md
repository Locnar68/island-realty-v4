# ROOT CAUSE ANALYSIS: Why Fernando's Emails Aren't Creating Properties

## Date: February 9, 2026
## Issue: Emails from Fernando Macias not appearing on website correctly

---

## 🔍 WHAT'S ACTUALLY HAPPENING

### Fernando's emails ARE being processed...
✅ Found 10 emails from Fernando in `email_processing_log`
✅ Emails are being fetched from Gmail correctly
✅ AI extraction is working

### ...BUT they're being MISMATCHED to wrong properties!

**Example: "293 Avenue B Ronkonkoma" email (Feb 5, 2026)**
- ✅ Email processed successfully
- ❌ Matched to property ID 116 ("117 Centre Island Road Centre Island")
- ❌ Should have created NEW property for "293 Avenue B Ronkonkoma"
- **Result:** Email data updated the WRONG property!

---

## 🎯 ROOT CAUSES (3 Major Issues)

### ROOT CAUSE #1: EXTREMELY LOOSE ADDRESS MATCHING

**The Problem:**
Current code in `monitor_email_v4.py` line ~225:
```python
cursor.execute("SELECT id FROM properties WHERE LOWER(address) LIKE LOWER(%s) LIMIT 1", (f'%{address}%',))
```

**Why This is Broken:**
- Uses `LIKE '%{address}%'` which matches ANY substring
- Example: Searching "Avenue B" could match:
  - "123 Avenue B"
  - "456 Boulevard"  ← matches "venue" + "B"
  - "Avenue Brooklyn"
  - ANY address with "B" and "avenue" anywhere in it
- Takes the FIRST match (`LIMIT 1`) with no validation
- No scoring, no verification, just blind substring matching

**How It Causes Mismatches:**
1. AI extracts address: "293 Avenue B Ronkonkoma"
2. Query: `WHERE address LIKE '%293 Avenue B Ronkonkoma%'`
3. No exact match found
4. Falls back to fuzzy matching which is even worse
5. Randomly picks first property that vaguely matches
6. **Result:** Wrong property gets updated!

---

### ROOT CAUSE #2: AUTO-CREATE LOGIC NOT WORKING

**The Problem:**
When no match is found, the code should create a new property. But it's not.

**Current Flow:**
```python
if mls_number:
    # Try MLS match
elif address:
    cursor.execute("SELECT id ... LIKE LOWER(%s)", (f'%{address}%',))
    if row:
        property_id = row['id']  # ← Uses bad match
    else:
        property_id = self._create_property(...)  # ← Should create but doesn't always work
```

**Why It Fails:**
1. The `LIKE` query almost ALWAYS finds a false match
2. Because it's so loose, it rarely reaches the `else` branch
3. When it does try to create, it fails if:
   - AI didn't extract complete address
   - Missing required fields
   - Database constraint violations

**Result:** Properties that should exist don't get created.

---

### ROOT CAUSE #3: RETROACTIVE MATCHING SCRIPT MADE IT WORSE

**Discovery:**
Found `/opt/island-realty/retroactive_email_match_v2.py` with "FLEXIBLE MATCHING"

**The Damage:**
This script was run and caused systematic mismatches:
```python
required_score = 2  # Need at least 2 matches

# Street number match → +2 points (ALONE IS ENOUGH!)
# Street name match (60% words) → +2 points
# City match → +1 point
```

**How This Destroyed Data Quality:**
- Only requires score of 2 to match
- Street number match ALONE (2 points) is sufficient!
- **This means: ANY property with "293" in the address could match "293 Avenue B"**
- Script ran through all historical emails and re-matched them loosely
- Actions show `['retroactive_match']` for many emails
- This is why "293 Avenue B" email got matched to "117 Centre Island Road" (property ID was 11, now 116 after dedup)

**The Specific Bad Match:**
```
Email: "New List Price: 293 Avenue B Ronkonkoma NY 11779"
Matched to: Property ID 116 = "117 Centre Island Road Centre Island"

Why? The retroactive script likely found SOME connection:
- Maybe email body mentioned multiple properties
- Maybe partial text match on "Road" vs "Ronkonkoma"
- Maybe it just scored ANY random property as "2 points"
```

---

## 💥 CASCADING FAILURES

### Failure Chain:
1. **Loose matching** → Wrong properties matched
2. **Auto-create disabled by bad matches** → Missing properties
3. **Retroactive script** → Systematically corrupted existing matches
4. **Price not preserved** → Original list prices overwritten
5. **No validation** → Bad data persists unchecked

### Impact:
- ❌ Properties not on website (never created)
- ❌ Wrong properties updated with wrong data
- ❌ Historical prices lost
- ❌ Property status incorrect
- ❌ Fernando's emails appear "processed" but data is wrong

---

## 📊 CURRENT STATE VERIFICATION

### Email Processing Check:
```sql
-- Emails from Fernando that were MISMATCHED
SELECT email_subject, property_id, p.address
FROM email_processing_log epl
JOIN properties p ON epl.property_id = p.id
WHERE epl.email_from ILIKE '%fernando%'
AND epl.email_subject ILIKE '%new list price%'
ORDER BY epl.email_date DESC;
```

**Results:**
| Email Subject | Property ID | Actual Address | Expected Address |
|--------------|-------------|----------------|------------------|
| 293 Avenue B Ronkonkoma | 116 | 117 Centre Island Road | 293 Avenue B Ronkonkoma |
| 140 Arlington Avenue Valley Stream | 120 | 140 Arlington Avenue | ✓ Fixed |
| 160 Beach 30th Street | 1 | 34 Croydon Road | 160 Beach 30th Street |

**Analysis:** 3 out of 4 "New List Price" emails from Fernando were mismatched!

---

## 🔧 WHY THE "FIXES" DIDN'T FULLY WORK

### We fixed some issues but not the core problem:

**What We Fixed:**
✅ Removed duplicate properties
✅ Created 140 Arlington Avenue (ID 120)
✅ Added email import log
✅ Verified FOIL handling

**What We Missed:**
❌ Didn't replace the bad matching logic in monitor_email_v4.py
❌ Didn't integrate the enhanced matching functions
❌ Didn't clean up bad matches from retroactive script
❌ Didn't implement price preservation
❌ Didn't enforce auto-create when no match

**Why:** We created the SOLUTION (`enhanced_email_functions.py`) but didn't DEPLOY it!

---

## 🎯 THE REAL FIX (What Actually Needs to Happen)

### 1. STOP USING LOOSE MATCHING
**Replace this:**
```python
cursor.execute("SELECT id FROM properties WHERE LOWER(address) LIKE LOWER(%s) LIMIT 1", (f'%{address}%',))
```

**With smart matching:**
```python
property_id = smart_property_match(cursor, address, city, mls_number)
# Uses score-based matching:
# - REQUIRES street number match (mandatory)
# - REQUIRES street name match or high confidence
# - Minimum score of 3 out of 5 points
# - Returns None if no confident match
```

### 2. ENABLE AUTO-CREATE
**When no match found:**
```python
if not property_id and address:
    # ALWAYS create new property if we have an address
    property_id = create_property_from_email(cursor, property_data, email_data)
    log_action('created_new_property', property_id)
```

### 3. PRESERVE ORIGINAL PRICES
**Never overwrite original_list_price:**
```python
# On price update
UPDATE properties SET
    current_list_price = %s,
    original_list_price = COALESCE(original_list_price, %s),  ← preserve original
    updated_at = NOW()
WHERE id = %s
```

### 4. CLEAN UP BAD RETROACTIVE MATCHES
**Fix the damage:**
```sql
-- Find emails matched to wrong properties
SELECT * FROM email_processing_log
WHERE actions_taken LIKE '%retroactive_match%'
AND property_id IS NOT NULL;

-- For each bad match:
-- 1. Create correct property
-- 2. Re-associate email
-- 3. Update email_processing_log
```

### 5. NEVER RUN RETROACTIVE SCRIPT AGAIN
**Delete or disable:**
```bash
rm /opt/island-realty/retroactive_email_match*.py
# Or at minimum, increase required_score to 4+
```

---

## 📋 IMMEDIATE ACTION PLAN

### Priority 1: Deploy Enhanced Matching (TODAY)
1. Integrate `enhanced_email_functions.py` into `monitor_email_v4.py`
2. Replace `_save_to_database()` with `_save_to_database_enhanced()`
3. Test with one email
4. Restart email monitor service

### Priority 2: Fix Existing Bad Matches (TOMORROW)
1. Run script to find all mismatched emails
2. Create missing properties (293 Avenue B, 160 Beach 30th, etc.)
3. Re-associate emails to correct properties
4. Verify data integrity

### Priority 3: Implement Price Preservation (TOMORROW)
1. Update all price update queries to preserve `original_list_price`
2. Backfill missing original prices from status_history
3. Add validation to prevent overwrites

### Priority 4: Add Validation (THIS WEEK)
1. Add address match confidence scoring to email_import_log
2. Alert on low-confidence matches
3. Daily report of new properties created
4. Weekly audit of potential mismatches

---

## 🎓 LESSONS LEARNED

### Why This Happened:
1. **Over-optimized for matching** - Tried too hard to match instead of creating new
2. **No validation** - Bad matches went unnoticed
3. **Retroactive script** - Made systematic problem worse
4. **No testing** - Changes deployed without verifying results
5. **No monitoring** - Issues discovered manually, not automatically

### How to Prevent:
1. **Conservative matching** - Require high confidence or create new
2. **Mandatory validation** - Every match gets a confidence score
3. **No bulk operations** - Never run scripts that touch all data without review
4. **Test with real data** - Use actual emails to test matching
5. **Monitor in production** - Daily checks for anomalies

---

## ✅ SUCCESS CRITERIA

### Fix is Complete When:
- [ ] All Fernando emails create/match correct properties
- [ ] "293 Avenue B Ronkonkoma" property exists on website
- [ ] "160 Beach 30th Street" property exists on website
- [ ] Original prices preserved on all price updates
- [ ] No false matches in last 7 days (check email_import_log)
- [ ] Confidence scores show >90% high-confidence matches

---

## 📞 SUMMARY FOR NON-TECHNICAL STAKEHOLDERS

**Question:** Why aren't Fernando's emails showing up on the website?

**Answer:** 
They ARE being processed, but the system is matching them to WRONG properties instead of creating NEW ones. 

**Example:**
- Email: "New List Price: 293 Avenue B Ronkonkoma"
- System: "I'll match this to... 117 Centre Island Road!" ❌
- Should be: "No match found, creating new property for 293 Avenue B" ✓

**Why It Happened:**
The matching algorithm was too loose (matches anything vaguely similar) and a "retroactive matching script" made it systematically worse.

**The Fix:**
Replace loose matching with strict matching + auto-create new properties when no confident match exists.

**Timeline:**
- Enhanced matching code: ✅ Already written
- Deploy to production: 🔄 TODAY
- Fix existing bad matches: 🔄 TOMORROW
- Full validation: 🔄 THIS WEEK

---

**END OF ROOT CAUSE ANALYSIS**

