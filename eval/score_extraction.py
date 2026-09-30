"""Score the LIVE Claude extraction against the dataset answer key (run after step 3 in the app).
    python eval/score_extraction.py [path/to/answer_key_line_x_vendor.csv]
Reports: line-status accuracy, landed-price accuracy (within 1%), trap detection, and gate accuracy."""
import csv, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import store
KEY = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "aerchain_rfx_dataset" / "04_answer_key_DO_NOT_FEED_TO_SYSTEM" / "answer_key_line_x_vendor.csv"
key = {(r["vendor"], r["line_id"]): r for r in csv.DictReader(open(KEY))}
q = store.read_sql("SELECT * FROM quotes"); f = store.read_sql("SELECT * FROM flags"); v = store.read_sql("SELECT * FROM vendors")
priced_ok = priced_n = status_ok = 0; misses = []
for r in q.itertuples():
    k = key.get((r.vendor_id, r.line_id))
    if not k: continue
    exp_quoted = k["status"] not in ("not_quoted",) and k["raw_quote"] not in ("", None) and k["status"] != "unresolvable"
    got_quoted = r.status in ("quoted", "unclear") and r.landed_inr_per_uom == r.landed_inr_per_uom and r.landed_inr_per_uom is not None
    status_ok += (exp_quoted == got_quoted) or (k["status"] == "unresolvable" and r.status == "unresolvable")
    if k["landed_inr_per_uom"]:
        priced_n += 1; exp = float(k["landed_inr_per_uom"])
        if r.landed_inr_per_uom is not None and r.landed_inr_per_uom == r.landed_inr_per_uom and abs(r.landed_inr_per_uom - exp) / exp <= 0.01:
            priced_ok += 1
        else:
            misses.append((r.vendor_id, r.line_id, exp, r.landed_inr_per_uom, r.review_status, (r.derivation or "")[:90]))
flagged = lambda vid, lid: bool(len(q[(q.vendor_id == vid) & (q.line_id == lid) & (q.review_status.isin(["needs_review"]) | q.status.isin(["unresolvable", "unclear"]))]))
vflag = lambda vid, code: bool(len(f[(f.vendor_id == vid) & (f.code == code)]))
traps = {
 "V1 per-100 pricing normalised (L01)": abs((q[(q.vendor_id == "V1") & (q.line_id == "L01")].landed_inr_per_uom.iloc[0] or 0) - float(key[("V1", "L01")]["landed_inr_per_uom"])) < 0.05,
 "V1 L14 amount != qty x rate flagged": flagged("V1", "L14"),
 "V2 USD converted (L06 within 1%)": ("V2", "L06") not in [(m[0], m[1]) for m in misses],
 "V2 expired ISO -> NOT CLEARED": v.set_index("vendor_id").gate_status.get("V2") == "NOT CLEARED",
 "V2 conditional rebate not applied": vflag("V2", "conditional_discount"),
 "V3 7-ply declined (L11,L12,L30 not priced)": all(q[(q.vendor_id == "V3") & (q.line_id.isin(["L11", "L12", "L30"]))].landed_inr_per_uom.isna()),
 "V3 'item 10 same as item 6' resolved": ("V3", "L10") not in [(m[0], m[1]) for m in misses],
 "V3 BF20 alternate kept separate": ("V3", "L06") not in [(m[0], m[1]) for m in misses],
 "V4 handwritten correction flagged (L03)": flagged("V4", "L03"),
 "V4 blurred price flagged (L07)": flagged("V4", "L07"),
 "V4 inch sizes matched (L27 priced right)": ("V4", "L27") not in [(m[0], m[1]) for m in misses],
 "V5 new items unresolvable (L15, L29)": all(q[(q.vendor_id == "V5") & (q.line_id.isin(["L15", "L29"]))].status == "unresolvable"),
 "V5 freight-extra flagged": vflag("V5", "freight_unknown"),
 "V5 moisture 'below 10%' fails gate": v.set_index("vendor_id").gate_status.get("V5") == "NOT CLEARED",
}
print(f"Line status (quoted / not quoted) accuracy: {status_ok}/{len(q)}")
print(f"Landed price within 1% of truth: {priced_ok}/{priced_n}")
for m in misses: print("   miss", m)
print("Trap detection:"); [print(f"  {'✅' if ok else '❌'} {t}") for t, ok in traps.items()]
print(f"Traps caught: {sum(traps.values())}/{len(traps)}")
print("Gate:", dict(zip(v.vendor_id, v.gate_status)))
