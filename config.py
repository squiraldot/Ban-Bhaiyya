import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# Phase 1 defaults
DEFAULT_MAX_WARNINGS = 3
DEFAULT_MUTE_MINUTES = {1: 2, 2: 5}

# Phase 2 defaults
DEFAULT_SPAM_WINDOW_SECONDS = 8
DEFAULT_SPAM_MESSAGE_LIMIT = 6
DEFAULT_SPAM_MUTE_MINUTES = 10
DEFAULT_BLOCKED_LINK_ACTION = "delete"

FILTERS_FILE = BASE_DIR / "filters" / "abusive_words.txt"
SPAM_PATTERNS_FILE = BASE_DIR / "filters" / "spam_patterns.txt"
BLOCKED_DOMAINS_FILE = BASE_DIR / "filters" / "blocked_domains.txt"

# Phase 3 local fallback / migration source.
DATA_FILE = BASE_DIR / "data" / "warnings.json"

# Phase 3 persistent database: Supabase/PostgREST.
# Set these in Termux/server for production.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

DATABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_KEY)

LOG_RETENTION_LIMIT = 1000

# Phase 4
WELCOME_ENABLED_DEFAULT = True
ANTIRAID_ENABLED_DEFAULT = True
ANTIRAID_JOIN_LIMIT = 8
ANTIRAID_WINDOW_SECONDS = 20
ANTIRAID_LOCK_MINUTES = 10
AUTO_CLEANUP_ENABLED_DEFAULT = False


# ============================================================
# PHASE 5 — Advanced moderation, reputation & scheduled cleanup
# ============================================================

# Captcha / verification
VERIFICATION_ENABLED_DEFAULT = True
VERIFICATION_TIMEOUT_SECONDS = 120

# Join/account safety
MIN_ACCOUNT_AGE_DAYS_DEFAULT = 0
NEW_MEMBER_RESTRICTION_MINUTES_DEFAULT = 0

# Advanced message controls
REPEATED_MESSAGE_WINDOW_SECONDS = 60
REPEATED_MESSAGE_LIMIT = 3
MENTION_SPAM_LIMIT = 6
MAX_MESSAGE_LENGTH_DEFAULT = 4000

# Warning decay
WARNING_DECAY_ENABLED_DEFAULT = True
WARNING_DECAY_DAYS_DEFAULT = 30

# Cleanup
AUTO_CLEANUP_BATCH_SIZE = 100
CLEANUP_MAX_AGE_DAYS_DEFAULT = 30

# ============================================================
# PHASE 6 — Analytics, exports & health monitoring
# ============================================================
ANALYTICS_DEFAULT_DAYS = 7
ANALYTICS_MAX_DAYS = 90
HEALTHCHECK_INTERVAL_SECONDS = 300

# Deployment
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", "")
DASHBOARD_ORIGIN = os.getenv("DASHBOARD_ORIGIN", "")
