"""
Shared system constants for pricing defaults, directory paths, and configuration.
"""
import os

# System-wide pricing defaults
DEFAULT_PER_MINUTE_COST: float = 0.0
DEFAULT_INFRA_FIXED_COST: float = 0.0
DEFAULT_MAX_MONTHLY_MINUTES: float = 50.0

# Org-admin dashboard: minimum acceptable average compliance score for an agent
# to be considered "on target". Per-org override lives in
# organizations.target_compliance_score (NOT NULL DEFAULT 85.0) — this constant
# is now only the fallback/default value name, applied via the DB column
# default. The dashboard query reads the org's actual column value, it does
# not read this constant directly.
DEFAULT_TARGET_COMPLIANCE_SCORE: float = 85.0

# Temporary audio storage directory path (configurable via environment)
TEMP_AUDIO_DIR: str = os.getenv("TEMP_AUDIO_DIR", "./media/temp_audio")

# --- Prepaid billing defaults ---
INFRA_MONTH_OPTIONS: tuple = (1, 3, 6, 12)
MINUTE_PACK_OPTIONS: tuple = (1000, 4000)
DEFAULT_MINUTE_GRACE_LIMIT: float = 20.0
DEFAULT_INFRA_GRACE_DAYS: int = 7
MINIMUM_BILLABLE_MINUTES: float = 1.0

# Feature flag: when false, prepaid balance enforcement (402 at enqueue / pickup)
# is inert. Schema migration and ledger writes still occur regardless. This
# buys an operational window during cutover to record opening recharges before
# blocking goes live. Read at call-time (not import-time) so tests can flip it.
def prepaid_enforcement_enabled() -> bool:
    return os.getenv("PREPAID_ENFORCEMENT_ENABLED", "true").strip().lower() in ("1", "true", "yes")
