"""Run: python tests/test_pipeline.py  - checks normalisation/gate against the dataset answer key."""
import csv, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core.config as cfg
tmp = tempfile.mkdtemp(); cfg.DB_PATH = Path(tmp) / "t.db"
import core.store as store; store.DB_PATH = cfg.DB_PATH; store.init()
from core import normalize
from tests.fixture_ideal_extraction import build, KEY

rfx, ex = build()
store.put("rfx", rfx)
T = normalize.build(rfx, ex)
q = T["quotes"]; key = {(r["vendor"], r["line_id"]): r for r in csv.DictReader(open(KEY))}
bad = 0; checked = 0
for _, r in q.iterrows():
    k = key[(r.vendor_id, r.line_id)]
    if k["landed_inr_per_uom"] and r.landed_inr_per_uom == r.landed_inr_per_uom and r.landed_inr_per_uom is not None:
        checked += 1
        if abs(float(k["landed_inr_per_uom"]) - r.landed_inr_per_uom) > 0.02:
            bad += 1; print("MISMATCH", r.vendor_id, r.line_id, k["landed_inr_per_uom"], r.landed_inr_per_uom, r.derivation)
print(f"landed prices checked: {checked}, mismatches: {bad}")
print(T["vendors"][["vendor_id", "gate_status", "lines_priced", "needs_review", "annual_value_priced_lines"]].to_string())
print(T["vendors"][["vendor_id", "gate_reasons"]].to_string())
print(q.groupby(["vendor_id", "status"]).size().unstack(fill_value=0))
print(q.groupby(["vendor_id", "review_status"]).size().unstack(fill_value=0))
print(T["flags"].groupby(["vendor_id", "code"]).size().to_string())
exp_gate = {"V1": "CLEARED", "V2": "NOT CLEARED", "V3": "CONDITIONAL", "V4": "UNKNOWN", "V5": "NOT CLEARED"}
got = dict(zip(T["vendors"].vendor_id, T["vendors"].gate_status))
assert got == exp_gate, got
assert bad == 0
v5 = q[(q.vendor_id == "V5")]
print(v5[["line_id", "status", "landed_inr_per_uom", "is_estimate", "review_status"]].head(12).to_string())
store.write_tables(T); print("tables written OK")
