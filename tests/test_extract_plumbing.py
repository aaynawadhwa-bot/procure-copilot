"""Extraction plumbing with a fake client: every file type becomes valid content blocks, the forced tool call is
parsed, results are cached, then the pipeline + app shell run. (Accuracy itself: eval/score_extraction.py with Claude.)"""
import sys, tempfile, types, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import mock_streamlit; mock_streamlit.install()
import pandas.compat._optional as _o; _o.VERSIONS["jinja2"] = "3.0.0"
import core.config as cfg
tmp = Path(tempfile.mkdtemp()); cfg.DB_PATH = tmp / "t.db"
import core.store as store; store.DB_PATH = cfg.DB_PATH; store.init()
import core.llm as llm; llm.CACHE_DIR = tmp
from core import extract, comms, normalize
from core.rfx import load_demo_rfx, save_rfx
from tests.fixture_ideal_extraction import build
_, fx = build()
rfx = load_demo_rfx(); save_rfx(rfx); comms.receive_demo_replies()
calls = []
class Fake:
    messages = None
    def __init__(self): self.messages = self
    def create(self, **kw):
        c = kw["messages"][0]["content"]; kinds = [b["type"] for b in c]
        vend = next(v for v in rfx["invited_vendors"] if v["name"] in c[0]["text"])["vendor_id"]
        calls.append((vend, kinds, kw["tool_choice"], len(json.dumps(c))))
        return types.SimpleNamespace(stop_reason="tool_use", usage=None,
            content=[types.SimpleNamespace(type="tool_use", id="x", name="record_vendor_response", input=fx[vend]["result"])])
llm.set_client(Fake())
for v in rfx["invited_vendors"]:
    extract.extract_vendor(rfx, v)
extract.extract_vendor(rfx, rfx["invited_vendors"][0])   # cached -> no new call
for c in calls: print(c[0], c[1], c[2]["name"], f"{c[3]/1e6:.2f} MB")
assert len(calls) == 5, "cache not used"
assert "image" in calls[3][1] and "document" in calls[1][1]
T = normalize.rebuild(); print(T["vendors"][["vendor_id", "gate_status", "lines_priced"]].to_string())
import runpy
try: runpy.run_path(str(Path(__file__).resolve().parents[1] / "app.py")); print("app shell OK")
except mock_streamlit.Stop: print("app shell OK (stop)")
print("EXTRACTION PLUMBING PASSED")
