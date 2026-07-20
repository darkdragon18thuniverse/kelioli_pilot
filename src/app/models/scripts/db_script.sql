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
    stt_model_routing TEXT NOT NULL DEFAULT 'sarvam-2',         
    llm_model_routing TEXT NOT NULL DEFAULT 'google/gemini-2.5-flash-lite', 
    default_language TEXT DEFAULT NULL,
    
    -- Pricing & Safeguard Boundaries
    per_minute_cost REAL NOT NULL DEFAULT 0.0,   
    infra_fixed_cost REAL NOT NULL DEFAULT 0.0,  
    max_monthly_minutes REAL DEFAULT 50.0,       
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,            
    name TEXT NOT NULL,                          
    slug TEXT NOT NULL,                          
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
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
    department_id INTEGER,                       
    parameter_name TEXT NOT NULL,                 
    rule_description TEXT NOT NULL,               
    severity_level TEXT NOT NULL DEFAULT 'medium' CHECK (severity_level IN ('low', 'medium', 'critical')),
    is_active INTEGER DEFAULT 1 CHECK (is_active IN (0, 1)),                  
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY(department_id) REFERENCES departments(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS csv_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    user_id INTEGER,                            -- Operator who triggered the ingestion batch
    filename TEXT NOT NULL,
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