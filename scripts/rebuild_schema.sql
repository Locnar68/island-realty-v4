-- ============================================================
-- Island PMS V4 - Complete Schema Rebuild
-- Generated: April 9, 2026
-- ============================================================

-- Agents (referenced by properties)
CREATE TABLE IF NOT EXISTS agents (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255),
    email       VARCHAR(255),
    created_at  TIMESTAMP DEFAULT NOW()
);

-- Main properties table
CREATE TABLE IF NOT EXISTS properties (
    id                          SERIAL PRIMARY KEY,
    mls_number                  VARCHAR(50),
    temporary_id                VARCHAR(50),
    address                     TEXT NOT NULL,
    address_2                   TEXT,
    city                        VARCHAR(100),
    zip_code                    VARCHAR(20),
    property_type               VARCHAR(100),
    current_list_price          NUMERIC(12,2),
    original_list_price         NUMERIC(12,2),
    current_status              VARCHAR(50),
    status                      VARCHAR(50),
    is_active                   BOOLEAN DEFAULT TRUE,
    hold_harmless_required      BOOLEAN DEFAULT FALSE,
    has_attachments             BOOLEAN DEFAULT FALSE,
    attachment_count            INTEGER DEFAULT 0,
    gmail_message_id            VARCHAR(255),
    last_email_id               VARCHAR(255),
    financing_type              VARCHAR(100),
    agent_access                VARCHAR(255),
    seller_agent_compensation   VARCHAR(100),
    occupancy_status            VARCHAR(100),
    reo_status                  VARCHAR(100),
    data_source                 VARCHAR(50) DEFAULT 'manual',
    highest_best_due_at         TIMESTAMP,
    totm_since                  TIMESTAMP,
    primary_photo_url           TEXT,
    photo_gallery_json          JSONB,
    assigned_agent_id           INTEGER REFERENCES agents(id),
    created_at                  TIMESTAMP DEFAULT NOW(),
    updated_at                  TIMESTAMP DEFAULT NOW(),
    UNIQUE(mls_number)
);

CREATE INDEX IF NOT EXISTS idx_properties_status   ON properties(current_status);
CREATE INDEX IF NOT EXISTS idx_properties_active   ON properties(is_active);
CREATE INDEX IF NOT EXISTS idx_properties_updated  ON properties(updated_at DESC);

-- Property emails (emails linked to a property)
CREATE TABLE IF NOT EXISTS property_emails (
    id                  SERIAL PRIMARY KEY,
    property_id         INTEGER REFERENCES properties(id),
    gmail_message_id    VARCHAR(255) UNIQUE,
    email_subject       TEXT,
    email_from          TEXT,
    email_date          TIMESTAMP,
    has_attachments     BOOLEAN DEFAULT FALSE,
    attachment_count    INTEGER DEFAULT 0,
    attachment_names    JSONB,
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_property_emails_prop ON property_emails(property_id);
CREATE INDEX IF NOT EXISTS idx_property_emails_date ON property_emails(email_date DESC);

-- Email processing log (every email the monitor sees)
DROP TABLE IF EXISTS email_processing_log;
CREATE TABLE email_processing_log (
    id                  SERIAL PRIMARY KEY,
    email_id            VARCHAR(255) UNIQUE,
    email_subject       TEXT,
    email_from          TEXT,
    email_date          TIMESTAMP,
    processing_status   VARCHAR(50) DEFAULT 'success',
    property_id         INTEGER REFERENCES properties(id),
    actions_taken       JSONB,
    error_message       TEXT,
    processing_time_ms  INTEGER,
    ai_model_used       VARCHAR(100),
    processed_at        TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_log_date     ON email_processing_log(email_date DESC);
CREATE INDEX IF NOT EXISTS idx_email_log_status   ON email_processing_log(processing_status);
CREATE INDEX IF NOT EXISTS idx_email_log_prop     ON email_processing_log(property_id);

-- Attachments
CREATE TABLE IF NOT EXISTS attachments (
    id                      SERIAL PRIMARY KEY,
    property_id             INTEGER REFERENCES properties(id),
    filename                TEXT,
    file_path               TEXT,
    file_url                TEXT,
    file_size               INTEGER,
    mime_type               VARCHAR(100),
    category                VARCHAR(100),
    subcategory             VARCHAR(100),
    priority                INTEGER DEFAULT 0,
    source_email_id         VARCHAR(255),
    source_email_date       TIMESTAMP,
    notes                   TEXT,
    uploaded_by             VARCHAR(100),
    gmail_attachment_id     VARCHAR(255),
    gmail_message_id        VARCHAR(255),
    is_foil                 BOOLEAN DEFAULT FALSE,
    uploaded_at             TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attachments_prop  ON attachments(property_id);
CREATE INDEX IF NOT EXISTS idx_attachments_foil  ON attachments(is_foil);

-- Status history
CREATE TABLE IF NOT EXISTS status_history (
    id                      SERIAL PRIMARY KEY,
    property_id             INTEGER REFERENCES properties(id),
    old_status              VARCHAR(50),
    new_status              VARCHAR(50),
    source_email_id         VARCHAR(255),
    source_email_subject    TEXT,
    source_email_date       TIMESTAMP,
    changed_by              VARCHAR(100),
    notes                   TEXT,
    changed_at              TIMESTAMP DEFAULT NOW()
);

-- Property flags
CREATE TABLE IF NOT EXISTS property_flags (
    id                  SERIAL PRIMARY KEY,
    property_id         INTEGER REFERENCES properties(id) UNIQUE,
    source_email_id     VARCHAR(255),
    locked_at           TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT NOW()
);

-- Highest & Best deadlines
CREATE TABLE IF NOT EXISTS highest_best_deadlines (
    id                      SERIAL PRIMARY KEY,
    property_id             INTEGER REFERENCES properties(id),
    due_date                DATE,
    due_time                TIME,
    offer_rules             TEXT,
    submission_instructions TEXT,
    source_email_id         VARCHAR(255),
    is_active               BOOLEAN DEFAULT TRUE,
    expired_at              TIMESTAMP,
    created_at              TIMESTAMP DEFAULT NOW()
);

-- Important property info
CREATE TABLE IF NOT EXISTS important_property_info (
    id                      SERIAL PRIMARY KEY,
    property_id             INTEGER REFERENCES properties(id),
    category                VARCHAR(100),
    title                   TEXT,
    content                 TEXT,
    severity                VARCHAR(50) DEFAULT 'info',
    source_email_id         VARCHAR(255),
    source_email_subject    TEXT,
    created_at              TIMESTAMP DEFAULT NOW()
);

-- Compliance alerts
CREATE TABLE IF NOT EXISTS compliance_alerts (
    id                      SERIAL PRIMARY KEY,
    property_id             INTEGER REFERENCES properties(id),
    alert_type              VARCHAR(100),
    title                   TEXT,
    description             TEXT,
    severity                VARCHAR(50) DEFAULT 'high',
    is_active               BOOLEAN DEFAULT TRUE,
    source_email_id         VARCHAR(255),
    source_attachment_id    INTEGER,
    resolved_at             TIMESTAMP,
    resolution_notes        TEXT,
    created_at              TIMESTAMP DEFAULT NOW()
);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id              SERIAL PRIMARY KEY,
    table_name      VARCHAR(100),
    record_id       INTEGER,
    action          VARCHAR(50),
    old_values      JSONB,
    new_values      JSONB,
    source_email_id VARCHAR(255),
    triggered_by    VARCHAR(100),
    created_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================================
-- Verify
-- ============================================================
SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;