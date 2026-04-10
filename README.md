# Island Advantage Realty — Property Management System V4

**Last updated: April 10, 2026**

> ⚠️ **KEEP THIS FILE UP TO DATE.** If infrastructure, credentials, OS, database schema, or application logic changes — update this README immediately. This is the disaster recovery runbook.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [AWS Infrastructure](#aws-infrastructure)
3. [SSH Access](#ssh-access)
4. [Application Stack](#application-stack)
5. [Database](#database)
6. [API Keys & Credentials](#api-keys--credentials)
7. [Systemd Services](#systemd-services)
8. [Key Files & Paths](#key-files--paths)
9. [Canonical Status Names](#canonical-status-names)
10. [Data Flow Rules (Rob's Rules)](#data-flow-rules-robs-rules)
11. [Disaster Recovery Playbook](#disaster-recovery-playbook)
12. [Common Operations](#common-operations)
13. [Known Gotchas](#known-gotchas)

---

## System Overview

Island PMS V4 is a Flask/PostgreSQL web app running on AWS EC2 tracking REO (Real Estate Owned) properties for Island Advantage Realty. Rob is the licensed agent and primary end-user. Michael is the developer/administrator.

- **Public dashboard:** `http://34.225.73.128`
- **Admin panel:** `http://34.225.73.128/admin`
- Properties come from two sources: Rob's weekly ACT PDF spreadsheet uploads, and automated Gmail monitoring.

---

## AWS Infrastructure

| Resource | Value |
|---|---|
| **Instance ID** | `i-07fc131a66cc15c72` |
| **Instance name** | island-pms-prod-v4 |
| **Elastic IP** | `34.225.73.128` |
| **EIP Allocation** | `eipalloc-0d849a5fd377c52c0` |
| **Region** | us-east-1 |
| **OS** | Ubuntu 22.04 LTS |
| **Security group** | `sg-0cdb4022d68b86743` (island-pms-v4-sg) |
| **Key pair name** | island-realty-v4-key |
| **AWS Account ID** | 260484936073 |

**Security group inbound rules needed:**
- Port 80 (HTTP) — 0.0.0.0/0
- Port 22 (SSH) — your IP or use SSM (no direct SSH needed)
- Port 443 (HTTPS) — if/when SSL is added

**⚠️ CRITICAL: Elastic IP must be re-associated after any instance replacement.**
The IP `34.225.73.128` is what Rob and the system rely on. Always associate it to the new instance before anything else.

---

## SSH Access

### Primary method — AWS SSM (no key needed, works from Claude)
```
aws ssm start-session --instance-id i-07fc131a66cc15c72 --region us-east-1
```

### SSH key (canonical, April 2026)
- **Type:** ed25519
- **Location:** `C:\Users\micha.ssh\island_fixed_key`
- **Public key:** `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILSPrdygf1EP9bLLKRQsudFn1Tn9LGWqrXFTXc2LMYqr`
- **Connect:** `ssh -i "C:\Users\micha.ssh\island_fixed_key" ubuntu@34.225.73.128`
- **SCP large files:** `scp -i "C:\Users\micha.ssh\island_fixed_key" <file> ubuntu@34.225.73.128:/home/ubuntu/`
  Then: `sudo cp /home/ubuntu/<file> /opt/island-realty/<file>` (ubuntu can't write to /opt directly)

### Legacy keys (DO NOT USE)
- `C:\Users\micha\Downloads\island-new-key.pem` — old instance
- `D:\LAB\keys\island-new-key.pem` — stale copy
- `island-pms-v3.pem` — wrong instance

### SSM automation notes
- SSM runs as **root**
- Always use venv Python: `/opt/island-realty/venv/bin/python3`
- PostgreSQL via SSM: requires `-h localhost` + `export PGPASSWORD=...`
- Large files: SCP to `/home/ubuntu/` then SSM `sudo cp`
- SSM payload limit ~24KB — base64-encode scripts for anything larger
- Git via SSM: `export HOME=/root && git config --global --add safe.directory /opt/island-realty`

---

## Application Stack

| Component | Details |
|---|---|
| Language | Python 3.10 |
| Framework | Flask |
| Web server | Nginx → Flask on port 5000 |
| Database | PostgreSQL 14 |
| Cache | Redis (local, port 6379) — ACT reconciliation result caching |
| App path | `/opt/island-realty/` (root-owned) |
| Virtualenv | `/opt/island-realty/venv/` |
| GitHub | `https://github.com/Locnar68/island-realty-v4` |
| GitHub user | Locnar68 |

### Nginx config
```nginx
server {
    listen 80 default_server;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120;
    }
}
```
Config: `/etc/nginx/sites-enabled/`

---

## Database

| Field | Value |
|---|---|
| Host | localhost |
| Port | 5432 |
| Database | island_properties |
| User | island_user |
| Password | `Pepmi@12` |

```bash
export PGPASSWORD=Pepmi@12
psql -h localhost -U island_user -d island_properties
```

### Tables (11 total)

| Table | Purpose |
|---|---|
| `properties` | **Core table.** All property cards. |
| `property_emails` | Emails linked to properties |
| `attachments` | Files/PDFs attached to property emails |
| `email_processing_log` | Every email the monitor has seen — dedup source of truth |
| `status_history` | Audit trail of every status change |
| `highest_best_deadlines` | H&B deadline tracking |
| `important_property_info` | AI-extracted key info per property |
| `compliance_alerts` | Violations, ECB, safety flags |
| `property_flags` | Feature flags per property |
| `agents` | Agent records |
| `audit_log` | General system audit log |

### `properties` — key columns

```
id                      SERIAL PRIMARY KEY
address                 TEXT NOT NULL
city                    VARCHAR(100)
current_status          VARCHAR(50)      -- canonical status (see below)
current_list_price      NUMERIC(12,2)
financing_type          VARCHAR(100)     -- Cash Only / Cash/Rehab / Cash/Conventional
agent_access            VARCHAR(255)     -- Proceed / Proceed Hold Harmless / Occupied NO SHOWS
occupancy_status        VARCHAR(100)     -- Vacant / Occupied / Owner Occupied
hold_harmless_required  BOOLEAN
is_active               BOOLEAN          -- false = hidden from dashboard
reo_status              VARCHAR(100)     -- raw status from spreadsheet
data_source             VARCHAR(50)      -- act_spreadsheet / email / manual
primary_photo_url       TEXT             -- manually entered (NOT backed up automatically!)
photo_gallery_json      JSONB
mls_number              VARCHAR(50) UNIQUE
property_type           VARCHAR(100)
highest_best_due_at     TIMESTAMP
totm_since              TIMESTAMP
created_at / updated_at TIMESTAMP
```

### Rebuild schema
```bash
psql -h localhost -U island_user -d island_properties -f /opt/island-realty/scripts/rebuild_schema.sql
```

### Quick health check
```bash
export PGPASSWORD=Pepmi@12
psql -h localhost -U island_user -d island_properties -c \
  "SELECT current_status, COUNT(*) FROM properties WHERE is_active=TRUE GROUP BY current_status ORDER BY COUNT(*) DESC;"
```

Expected (as of April 10, 2026): 184 properties total — Auction/Available 76, Incontract 47, Available 44, 1st Accepted 15, H&B 1, ½ Signed 1.

---

## API Keys & Credentials

| Key | Value | Notes |
|---|---|---|
| DB password | `Pepmi@12` | In `.env` and systemd unit |
| Anthropic API key | stored in env | Email monitor AI extraction |
| GitHub PAT (current) | `ghp_REDACTED_SEE_VAULT_OR_PROJECT_MEMORY` | Active as of March 2026 |
| GitHub PAT (expired) | `ghp_EXPIRED_REDACTED` | DO NOT USE |
| Gmail OAuth token | `/opt/island-realty/config/token.pickle` | Re-auth if expired |
| Gmail credentials | `/opt/island-realty/config/gmail-credentials.json` | OAuth2 client config |

**Git push:**
```bash
export HOME=/root
git -C /opt/island-realty config --global --add safe.directory /opt/island-realty
git -C /opt/island-realty add -A
git -C /opt/island-realty commit -m "your message"
git -C /opt/island-realty push https://Locnar68:ghp_REDACTED_SEE_VAULT_OR_PROJECT_MEMORY@github.com/Locnar68/island-realty-v4.git main
```

**⚠️ If Gmail token.pickle expires:**
Run `python3 auth_gmail.py` interactively (needs browser — use EC2 Instance Connect).
Token saves to `/opt/island-realty/config/token.pickle`.
Then: `systemctl restart island-email-monitor`

---

## Systemd Services

### `island-realty` (Flask app)
```ini
[Unit]
Description=Island Realty Flask App
After=network.target postgresql.service redis.service

[Service]
User=root
WorkingDirectory=/opt/island-realty
ExecStart=/opt/island-realty/venv/bin/python3 run.py
Restart=always
RestartSec=5
Environment=FLASK_ENV=production
Environment=DB_PASSWORD=Pepmi@12
Environment=DB_NAME=island_properties
Environment=DB_USER=island_user
Environment=DB_HOST=localhost
```

### `island-email-monitor` (Gmail poller)
```ini
[Unit]
Description=Island Realty Email Monitor
After=network.target island-realty.service

[Service]
User=root
WorkingDirectory=/opt/island-realty
ExecStart=/opt/island-realty/venv/bin/python3 monitor_email_v4.py
Restart=always
RestartSec=10
```

**Commands:**
```bash
systemctl restart island-realty
systemctl restart island-email-monitor
journalctl -u island-realty --since '10 minutes ago' --no-pager
tail -f /opt/island-realty/logs/email_monitor_v4.log
```

---

## Key Files & Paths

```
/opt/island-realty/
├── app/
│   ├── __init__.py              # Main Flask routes — all API endpoints (~62KB)
│   ├── email_processor.py       # AI email parsing (Claude Sonnet)
│   ├── models.py                # DB models
│   ├── attachment_manager.py    # Gmail attachment retrieval
│   └── templates/
│       ├── dashboard.html       # Public dashboard
│       └── admin_dashboard.html # Admin panel (/admin)
├── scripts/
│   ├── act_reconciliation.py    # ACT PDF parser (9-col layout)
│   └── rebuild_schema.sql       # Full schema rebuild
├── monitor_email_v4.py          # Email monitor — Gmail polling, Rob's rules
├── run.py                       # Flask entry point
├── requirements.txt             # Python deps
├── .env                         # DB_PASSWORD=Pepmi@12
├── config/
│   ├── token.pickle             # Gmail OAuth token *** BACK THIS UP ***
│   └── gmail-credentials.json  # Gmail OAuth client
└── logs/
    └── email_monitor_v4.log
```

### Upload endpoints
| Route | Purpose |
|---|---|
| `POST /api/admin/upload-act-spreadsheet` | Rob's weekly ACT PDF — primary data source |
| `POST /api/admin/upload-spreadsheet` | Excel/CSV for price population |

---

## Canonical Status Names

Only these values are valid for `current_status`. Use exactly as shown.

| Status | Source | Notes |
|---|---|---|
| `Available` | Spreadsheet | Plain available |
| `Auction/Available` | Spreadsheet | Auction property |
| `1st Accepted` | Spreadsheet / Email | Offer accepted |
| `½ Signed` | **Spreadsheet only** | Half-signed contract |
| `Incontract` | **Spreadsheet only** | Under contract |
| `H&B` | **Email only** | Highest & Best |
| `TOTM` | Email | Temporarily off market |
| `Sold` | Spreadsheet | is_active → FALSE |
| `Closed` | Spreadsheet | is_active → FALSE |

**Normalization (`_normalize_status`):**
- `active`, `back on market`, `bom` → `Available`
- `auction available` → `Auction/Available`
- `in contract`, `pending`, `under contract` → `Incontract`
- `first accepted`, `1st accept` → `1st Accepted`
- `highest & best`, `h&b` → `H&B`
- `price reduced` → `None` (price-only update, no status change)

---

## Data Flow Rules (Rob's Rules)

Mandatory. Deployed March 31, 2026.

1. Spreadsheet is the **single source of truth** — reconciled weekly
2. Every property card must have a **price**
3. All details must match the spreadsheet (price, status, financing, agent access)
4. Upload sequence: **spreadsheet first** → emails layer on top
5. Closed/Sold → `is_active=FALSE` immediately
6. Card count must match spreadsheet count
7. 50% safety threshold on deactivation

### ACT PDF column layout (9 columns, as of April 2026)
```
Col 0: REO Status     Col 5: City
Col 1: Financing      Col 6: List Price
Col 2: Prop Style     Col 7: Occupancy
Col 3: Address 1      Col 8: Agent Access
Col 4: Address 2
```
Note: No List Price in PDF for In Contract / Auction Available — use Excel/CSV upload or manual edit.

### Email processing rules (`apply_rob_rules`)
| Subject | Body | Result |
|---|---|---|
| `New List Price:` | `hold harmless` | Available + Proceed Hold Harmless |
| `New List Price:` | `occupied` + `auction.com` | Auction/Available + Occupied NO SHOWS |
| `New List Price:` | `lock box` / `lockbox` | Available + Proceed |
| `New List Price:` | (else) | Available |
| `Highest & Best Notification:` | — | H&B |
| `BOM - Back on Market` | — | Available |
| `Status Update` | `1st accepted` in body | 1st Accepted |
| `Price Reduction` / `Price Reduced` | — | Price update only, no status change |

---

## Disaster Recovery Playbook

### Complete server loss

1. **Launch new EC2** — Ubuntu 22.04, same security group `sg-0cdb4022d68b86743`

2. **Re-associate Elastic IP immediately**
   ```
   aws ec2 associate-address --instance-id <NEW_ID> --allocation-id eipalloc-0d849a5fd377c52c0 --region us-east-1
   ```

3. **Install dependencies**
   ```bash
   apt update && apt install -y python3 python3-pip python3-venv postgresql redis-server nginx git
   systemctl enable postgresql redis-server nginx
   ```

4. **Clone and set up app**
   ```bash
   cd /opt
   git clone https://github.com/Locnar68/island-realty-v4.git island-realty
   cd island-realty
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   ```

5. **Set up PostgreSQL**
   ```bash
   sudo -u postgres psql -c "CREATE USER island_user WITH PASSWORD 'Pepmi@12';"
   sudo -u postgres psql -c "CREATE DATABASE island_properties OWNER island_user;"
   export PGPASSWORD=Pepmi@12
   psql -h localhost -U island_user -d island_properties -f /opt/island-realty/scripts/rebuild_schema.sql
   ```

6. **Create .env**
   ```bash
   echo "DB_PASSWORD=Pepmi@12" > /opt/island-realty/.env
   ```

7. **Install systemd services**
   ```bash
   # Copy service files from repo to /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable island-realty island-email-monitor
   systemctl start island-realty island-email-monitor
   ```

8. **Configure Nginx** — copy config, `nginx -t && systemctl reload nginx`

9. **Restore Gmail token**
   - Copy `token.pickle` from backup to `/opt/island-realty/config/token.pickle`
   - token.pickle is NOT in git — must be backed up separately
   - If no backup: run `venv/bin/python3 auth_gmail.py` interactively

10. **Have Rob re-upload latest ACT PDF** → admin panel → Upload ACT Spreadsheet

11. **Photos must be manually re-entered** — not stored in code or git

### Database only lost (server intact)

1. `psql -h localhost -U island_user -d island_properties -f /opt/island-realty/scripts/rebuild_schema.sql`
2. `venv/bin/pip install python-dotenv` (if missing)
3. `systemctl restart island-realty`
4. Rob re-uploads ACT PDF

---

## Common Operations

### Check system health
```bash
systemctl is-active island-realty island-email-monitor
export PGPASSWORD=Pepmi@12
psql -h localhost -U island_user -d island_properties -c \
  "SELECT COUNT(*) as total, COUNT(current_list_price) FILTER (WHERE current_list_price>0) as has_price, COUNT(agent_access) FILTER (WHERE agent_access IS NOT NULL) as has_access FROM properties WHERE is_active=TRUE;"
```

### Manually fix a property status
```bash
export PGPASSWORD=Pepmi@12
psql -h localhost -U island_user -d island_properties -c \
  "UPDATE properties SET current_status='H&B', updated_at=NOW() WHERE address ILIKE '%pardee%';"
```

### Re-authorize Gmail
```bash
cd /opt/island-realty && venv/bin/python3 auth_gmail.py
systemctl restart island-email-monitor
```

### Push to GitHub
```bash
export HOME=/root
git -C /opt/island-realty config --global --add safe.directory /opt/island-realty
git -C /opt/island-realty add -A
git -C /opt/island-realty commit -m "description of change"
git -C /opt/island-realty push https://Locnar68:ghp_REDACTED_SEE_VAULT_OR_PROJECT_MEMORY@github.com/Locnar68/island-realty-v4.git main
```

### Take a DB backup
```bash
export PGPASSWORD=Pepmi@12
pg_dump -h localhost -U island_user island_properties > /home/ubuntu/backup_$(date +%Y%m%d).sql
```
**Do this weekly. Store off-server (S3 or local).**

---

## Known Gotchas

1. **`½` character** — `act_reconciliation.py` stores it as the literal escape `\u00bd`. Use `ESC_HALF = '\u00bd'` in f-strings when patching. All other files use actual UTF-8 ½.

2. **`/api/properties` ignores query params** — all filtering/sorting is client-side in JavaScript.

3. **SSM runs as root** — git needs `export HOME=/root`.

4. **PostgreSQL peer auth** — always add `-h localhost` to psql in SSM or peer auth fails.

5. **`financing_type` must be in `allowed_fields`** — in the admin update endpoint in `__init__.py` or it silently won't save.

6. **ACT PDF has no prices for In Contract / Auction Available** — preserve existing prices for these statuses, never zero them out.

7. **`email_date` guard was removed April 2026** — old `CASE WHEN email_date > '2026-01-27'` blocked spreadsheet updates. Replaced with H&B preservation logic. Do NOT re-introduce.

8. **Photos are DB-only** — `primary_photo_url` and `photo_gallery_json` live only in the DB. A DB wipe loses them permanently. Take `pg_dump` backups.

9. **50% deactivation safety threshold** — reconciliation aborts if >50% of active properties would be deactivated. Prevents mass-deactivation from address matching bugs.

10. **`token.pickle` not in git** — gitignored for security. Back it up to S3 or secure storage after any re-authorization.

---

## Backup Checklist

- [ ] Weekly `pg_dump` → store off-server
- [ ] `token.pickle` backed up after any re-auth
- [ ] Code pushed to GitHub after every change
- [ ] Rob keeps photo URLs in a spreadsheet as fallback

---

*Maintained by Michael (developer/administrator). Update this file whenever infrastructure, credentials, or application behavior changes.*
