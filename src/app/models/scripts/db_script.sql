-- ==========================================
-- 0. ENGINE ARCHITECTURE INITIALIZATION
-- ==========================================
-- Enforce relational database integrity and cascade behaviors at runtime
PRAGMA foreign_keys = ON;

-- ==========================================
-- 1. AUTHENTICATION & ACCESS CONTROL (RBAC)
-- ==========================================

CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,                  -- 'superadmin', 'admin', 'manager', 'agent'
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed System Standard Roles
INSERT OR IGNORE INTO roles (id, name, description) VALUES 
(1, 'superadmin', 'Global System Administrator'),
(2, 'admin', 'Organisation Administrator'),
(3, 'manager', 'Department Manager'),
(4, 'agent', 'Operational Telephony Agent');

-- ==========================================
-- 2. CORE SYSTEM INFRASTRUCTURE & MULTI-TENANCY
-- ==========================================

CREATE TABLE IF NOT EXISTS organizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,                  -- URL vanity path component
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'limit_exceeded')),
    tier TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'growth', 'enterprise')),
    billing_email TEXT,
    
    -- Dynamic Multi-Tier AI Service Tier Engines Routing Nodes
    stt_model_routing TEXT NOT NULL DEFAULT 'saaras:v3',         
    llm_provider TEXT NOT NULL DEFAULT 'openrouter' CHECK (llm_provider IN ('openrouter','gemini')),
    llm_model_routing TEXT NOT NULL DEFAULT 'openrouter/free',
    call_eval_effort TEXT NOT NULL DEFAULT 'medium' CHECK (call_eval_effort IN ('minimal','low','medium','high')),
    company_context TEXT DEFAULT NULL,
    default_language TEXT DEFAULT NULL,
    
    -- Pricing & Safeguard Boundaries
    per_minute_cost REAL NOT NULL DEFAULT 0.0,
    infra_fixed_cost REAL NOT NULL DEFAULT 0.0,
    max_monthly_minutes REAL DEFAULT 50.0,
    target_compliance_score REAL NOT NULL DEFAULT 85.0,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,            
    name TEXT NOT NULL,                          
    slug TEXT NOT NULL,                          
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    department_context TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(organization_id, name),
    UNIQUE(organization_id, slug)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id INTEGER NOT NULL,                     
    organization_id INTEGER,                     -- Nullable for app-wide superadmins
    department_id INTEGER,                       -- Nullable for superadmins & org admins
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'invited')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(role_id) REFERENCES roles(id),
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL
);

-- ==========================================
-- 3. PLAYBOOKS & BATCH MANAGEMENT ENGINE
-- ==========================================

CREATE TABLE IF NOT EXISTS compliance_parameters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,            
    department_id INTEGER NOT NULL,              
    parameter_name TEXT NOT NULL,                 
    rule_description TEXT NOT NULL,               
    severity_level TEXT NOT NULL DEFAULT 'medium' CHECK (severity_level IN ('low', 'medium', 'high', 'critical')),
    is_active INTEGER DEFAULT 1 CHECK (is_active IN (0, 1)),                  
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS csv_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    user_id INTEGER,                            -- Operator who triggered the ingestion batch
    filename TEXT NOT NULL,
    file_hash TEXT,
    total_records INTEGER DEFAULT 0,
    processed_records INTEGER DEFAULT 0,
    failed_records INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'completed', 'failed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- ==========================================
-- 4. TRANSACTIONS, AUDITING & DATA PIPELINE
-- ==========================================

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,            
    department_id INTEGER NOT NULL,              -- Data protected via RESTRICT modifier below
    user_id INTEGER,                             -- Preserves metrics if an agent profile is removed
    csv_upload_id INTEGER,                       
    audio_url TEXT NOT NULL,
    duration_seconds REAL DEFAULT 0.0,
    file_size_bytes INTEGER DEFAULT 0,
    
    -- Industry-Agnostic Context Tag Extracted dynamically by OpenRouter
    procedure_enquired TEXT DEFAULT NULL,        
    
    processing_status TEXT NOT NULL DEFAULT 'pending' CHECK (processing_status IN ('pending', 'transcribing', 'evaluating', 'completed', 'failed')), 
    error_message TEXT,                           
    
    -- Immutable Historical Runtime Infrastructure Log Keys
    runtime_stt_model TEXT,                      
    runtime_llm_model TEXT,                      
    
    -- Internal Token Logging for Margin Checks
    upstream_tokens_prompt INTEGER DEFAULT 0,     
    upstream_tokens_completion INTEGER DEFAULT 0, 
    internal_execution_cost REAL DEFAULT 0.0,     
    
    transcript TEXT,                              
    transcript_chunks TEXT DEFAULT NULL,
    total_parameters_checked INTEGER DEFAULT 0,
    total_parameters_passed INTEGER DEFAULT 0,
    compliance_score_percentage REAL DEFAULT 0.0, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE RESTRICT, 
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY(csv_upload_id) REFERENCES csv_uploads(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS call_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    parameter_id INTEGER NOT NULL,            -- Data protected via RESTRICT modifier below
    did_follow_rule INTEGER NOT NULL CHECK (did_follow_rule IN (0, 1)),             
    failure_offset_seconds INTEGER DEFAULT NULL,  -- Relative integer track for seamless playhead syncing
    failure_reason TEXT,                          
    failed_line_text TEXT,                        -- Exact verbatim quote from transcript if rule failed
    parameter_snapshot_text TEXT,                 -- Frozen copy of the evaluation rule criteria
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY(call_id) REFERENCES calls(id) ON DELETE CASCADE,
    FOREIGN KEY(parameter_id) REFERENCES compliance_parameters(id) ON DELETE RESTRICT 
);

-- ==========================================
-- 5. HISTORICAL LONG-TERM METRICS SNAPSHOTS
-- ==========================================

CREATE TABLE IF NOT EXISTS billing_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    tier_at_billing TEXT NOT NULL,
    infra_fixed_cost_charged REAL NOT NULL,
    per_minute_cost_charged REAL NOT NULL,
    total_minutes_consumed REAL NOT NULL,
    total_spend_calculated REAL NOT NULL,
    billing_period_start DATE NOT NULL,
    billing_period_end DATE NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid', 'paid', 'voided', 'overdue')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

-- ==========================================
-- 5b. PREPAID BILLING (recharges + minute ledger)
-- ==========================================

CREATE TABLE IF NOT EXISTS prepaid_recharges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    recharge_type TEXT NOT NULL CHECK (recharge_type IN ('infra','minutes')),
    minutes_purchased REAL,                 -- minutes packs only
    months_purchased INTEGER,               -- infra only
    infra_period_start DATE,                -- infra only
    infra_period_end DATE,                  -- infra only
    unit_price_at_purchase REAL NOT NULL,   -- per_minute_cost, or infra_fixed_cost per month
    amount_charged REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    payment_provider TEXT NOT NULL DEFAULT 'manual',
    payment_reference TEXT,
    payment_status TEXT NOT NULL DEFAULT 'paid'
        CHECK (payment_status IN ('pending','paid','failed','refunded')),
    paid_at TIMESTAMP,
    notes TEXT,
    created_by_user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    voided_at TIMESTAMP,
    voided_by_user_id INTEGER,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS minute_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('recharge','usage','adjustment','void')),
    minutes_delta REAL NOT NULL,   -- + credit, - debit
    balance_after REAL NOT NULL,   -- audit convenience, written in same txn
    call_id INTEGER,
    recharge_id INTEGER,
    note TEXT,
    created_by_user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(call_id) REFERENCES calls(id) ON DELETE SET NULL,
    FOREIGN KEY(recharge_id) REFERENCES prepaid_recharges(id) ON DELETE SET NULL
);

-- Idempotency: one usage debit per call, one credit per recharge. Non-negotiable.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_call_usage
    ON minute_ledger(call_id) WHERE entry_type = 'usage' AND call_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_recharge_credit
    ON minute_ledger(recharge_id) WHERE entry_type = 'recharge' AND recharge_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ledger_org ON minute_ledger(organization_id, id);
CREATE INDEX IF NOT EXISTS idx_recharges_org ON prepaid_recharges(organization_id, id);

-- Cross-process shared state used to throttle outbound calls to external APIs
-- (Gemini, Sarvam STT, etc.) to a single effective rate regardless of how many
-- gunicorn worker processes are running.
CREATE TABLE IF NOT EXISTS rate_limit_state (
    rate_key TEXT PRIMARY KEY,
    last_request_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_usage_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    user_id INTEGER,                              
    usage_date DATE NOT NULL,
    total_minutes REAL DEFAULT 0.0,
    total_calls_processed INTEGER DEFAULT 0,
    total_calls_failed INTEGER DEFAULT 0,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE(organization_id, department_id, user_id, usage_date) ON CONFLICT REPLACE
);

-- ==========================================
-- 6. PERFORMANCE CRITICAL DATABASE INDEXES
-- ==========================================
CREATE INDEX IF NOT EXISTS idx_calls_org_date ON calls(organization_id, created_at);
CREATE INDEX IF NOT EXISTS idx_calls_user_id ON calls(user_id);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(processing_status);
CREATE INDEX IF NOT EXISTS idx_evaluations_call_id ON call_evaluations(call_id);
CREATE INDEX IF NOT EXISTS idx_daily_metrics_lookup ON daily_usage_metrics(organization_id, usage_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_csv_uploads_org_hash ON csv_uploads(organization_id, file_hash);

-- Org-admin dashboard aggregation support (additive, safe on a live DB)
CREATE INDEX IF NOT EXISTS idx_evaluations_parameter_id ON call_evaluations(parameter_id);
CREATE INDEX IF NOT EXISTS idx_calls_department  ON calls(department_id, created_at);
CREATE INDEX IF NOT EXISTS idx_params_org_dept   ON compliance_parameters(organization_id, department_id);
CREATE INDEX IF NOT EXISTS idx_users_org         ON users(organization_id);
CREATE INDEX IF NOT EXISTS idx_users_department  ON users(department_id);

-- ==========================================
-- 7. AUTOMATIC UPDATED_AT TIMESTAMP TRIGGERS
-- ==========================================
CREATE TRIGGER IF NOT EXISTS trg_organizations_updated_at
AFTER UPDATE ON organizations
BEGIN
    UPDATE organizations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_departments_updated_at
AFTER UPDATE ON departments
BEGIN
    UPDATE departments SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_users_updated_at
AFTER UPDATE ON users
BEGIN
    UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_calls_updated_at
AFTER UPDATE ON calls
BEGIN
    UPDATE calls SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;