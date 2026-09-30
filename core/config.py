"""Central configuration. Everything tunable lives here."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INBOX_DIR = DATA_DIR / "inbox"
EXPORT_DIR = DATA_DIR / "exports"
CACHE_DIR = DATA_DIR / "llm_cache"
DB_PATH = DATA_DIR / "app.db"
DEMO_DIR = ROOT / "demo_data"
DEMO_RFX = DEMO_DIR / "01_buyer_rfx" / "rfx.json"
DEMO_HISTORY = DEMO_DIR / "02_buyer_history"
DEMO_RESPONSES = DEMO_DIR / "03_vendor_responses"

for d in (DATA_DIR, INBOX_DIR, EXPORT_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
MODEL_FAST = os.getenv("CLAUDE_MODEL_FAST", MODEL)
API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Bump when the extraction prompt/schema changes so cached results are invalidated.
EXTRACTION_PROMPT_VERSION = "x3"

# Normalisation policy defaults (buyer can change in the sidebar)
DEFAULTS = {
    "freight_estimate_inr_per_kg": 1.5,   # used ONLY when a vendor says "freight extra" with no amount
    "outlier_threshold_pct": 35,          # deviation vs median of other vendors that raises a flag
    "include_estimates_in_award": False,  # award screen excludes estimated/derived-incomplete prices unless toggled
    "gate_mode": "strict",                # strict: conditional vendors are NOT cleared; lenient: they are
}

CONF_HIGH = 0.85
CONF_MED = 0.60
