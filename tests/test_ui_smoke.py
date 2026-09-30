"""Executes every page headless against a populated DB (fixture data) with a mocked streamlit."""
import sys, tempfile, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import mock_streamlit
st = mock_streamlit.install()
import pandas.compat._optional as _o; _o.VERSIONS["jinja2"] = "3.0.0"  # sandbox has old jinja2
import core.config as cfg
tmp = Path(tempfile.mkdtemp()); cfg.DB_PATH = tmp / "t.db"
import core.store as store; store.DB_PATH = cfg.DB_PATH; store.init()
from core import normalize, comms
from tests.fixture_ideal_extraction import build
rfx, ex = build(); store.put("rfx", rfx)
comms.receive_demo_replies(); comms.send_rfx(rfx)
for k, v in ex.items(): store.put(f"extraction:{k}", v)
normalize.rebuild()
from ui import p_create, p_send, p_review, p_compare, p_quality, p_analyst, p_award
from ui.common import inr, show_evidence
assert inr(12345678) == "₹1,23,45,678", inr(12345678); assert inr(999) == "₹999"
fails = 0
for name, fn in [("create", p_create.page), ("send", p_send.page), ("review", p_review.page), ("compare", p_compare.page),
                 ("quality", p_quality.page), ("analyst", p_analyst.page), ("award", p_award.page)]:
    try:
        fn(); print("OK  ", name)
    except mock_streamlit.Stop:
        print("OK* ", name, "(stopped)")
    except Exception:
        fails += 1; print("FAIL", name); traceback.print_exc()
# evidence panel for every kind of source
for vid, lid in [("V1", "L01"), ("V2", "L05"), ("V3", "L06"), ("V4", "L03"), ("V5", "L11"), ("V3", "L11")]:
    try: show_evidence(vid, lid); print("OK   evidence", vid, lid)
    except Exception: fails += 1; print("FAIL evidence", vid, lid); traceback.print_exc()
# award page with single vendor strategy + lenient
st.radio = lambda label, options, index=0, **k: list(options)[-1] if label in ("Strategy", "Gate mode") else list(options)[index]
try: p_award.page(); print("OK   award single/lenient")
except Exception: fails += 1; traceback.print_exc()
print("UI SMOKE", "PASSED" if not fails else f"FAILED ({fails})")
