"""TEST-ONLY fixture: builds what a *perfect* extractor would return for the demo vendors, straight from the
dataset answer key. Used to unit-test the deterministic pipeline (normalisation, gate, flags) without API calls.
The app NEVER uses this - in the app every extraction comes from Claude reading the actual files."""
import csv, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import readers
from core.rfx import load_demo_rfx
KEY = ROOT.parent / "aerchain_rfx_dataset" / "04_answer_key_DO_NOT_FEED_TO_SYSTEM" / "answer_key_line_x_vendor.csv"
RESP = ROOT / "demo_data" / "03_vendor_responses"
FOLDER = {"V1": "V1_Shivam_Corrupack", "V2": "V2_Pacific_Fibreboard", "V3": "V3_Deccan_Corrugated", "V4": "V4_Rathi_Kraft", "V5": "V5_OmSai_Corrugators"}

def corpus(v):
    fs = sorted((RESP / FOLDER[v]).glob("*"))
    return {f.name: readers.to_text(f) for f in fs}, {f.name: readers.kind_of(f) for f in fs}

def find(c, *needles):
    for fname, text in c.items():
        for ln in text.splitlines():
            if all(n.lower() in ln.lower() for n in needles):
                return fname, ln
    return list(c)[0], needles[0]

def build():
    rfx = load_demo_rfx(); L = {l["line_id"]: l for l in rfx["line_items"]}
    key = list(csv.DictReader(open(KEY)))
    out = {}
    for v in FOLDER:
        c, kinds = corpus(v); lqs = []
        for r in [k for k in key if k["vendor"] == v]:
            lid = r["line_id"]; l = L[lid]; raw = r["raw_quote"] or ""
            base = {"rfx_line_id": lid, "match_confidence": 0.95, "extraction_confidence": 0.95, "legibility": "clear", "alternate_offers": []}
            if r["status"] in ("not_quoted",) or not raw:
                lqs.append({**base, "status": "declined" if v == "V3" else "not_quoted", "quote_form": "none", "price_unit": "none"}); continue
            if v == "V1":
                dims = f"{l['L_mm']}X{l['W_mm']}"; f_, ln = find(c, dims) if l["type"] != "PARTITION" else find(c, "PARTITION", f"{l['L_mm']}X{l['W_mm']}")
                if "per 100" in raw: p, u = float(re.findall(r"[\d.]+", raw)[0]), "per_100"
                elif "/Kg" in raw: p, u = 40.5, "per_kg"
                else: p, u = float(re.findall(r"[\d.]+", raw)[0]), "per_piece"
                e = {**base, "status": "quoted", "quote_form": "explicit_price", "price": p, "price_unit": u, "vendor_size_text": f"{l['L_mm']}X{l['W_mm']}X{l['H_mm']} MM" if l["H_mm"] else None,
                     "source": {"file": f_, "location": "Quotation!row", "snippet": ln.split(": ", 1)[-1]}}
                if lid == "L14":
                    e["vendor_qty"] = l["annual_qty"]; e["vendor_amount"] = round(l["annual_qty"] * (p + 1.05))
                lqs.append(e)
            elif v == "V2":
                f_, ln = find(c, lid + " ")
                p = float(re.findall(r"[\d.]+", raw)[0]); u = "per_running_metre" if "running" in raw else "per_piece"
                lqs.append({**base, "status": "quoted", "quote_form": "explicit_price", "price": p, "price_unit": u, "currency": "USD", "source": {"file": f_, "location": "page 1", "snippet": ln}})
            elif v == "V3":
                p = float(re.findall(r"[\d.]+", raw)[0]); pstr = f"{p:,.2f}"
                f_, ln = find(c, pstr)
                e = {**base, "status": "quoted", "quote_form": "explicit_price", "price": p, "price_unit": "per_piece", "source": {"file": f_, "location": "P", "snippet": ln[ln.find("]")+2:][:400]}}
                if lid == "L10":
                    e.update(quote_form="same_as_other_line", referenced_line_id="L06", price=None)
                if lid == "L06":
                    e["alternate_offers"] = [{"description": "BF 20 instead of BF 22", "price": 30.35, "price_unit": "per_piece", "deviation": "BF20 vs BF22"}]
                lqs.append(e)
            elif v == "V4":
                f_ = [k for k, kk in kinds.items() if kk == "image"][0]
                if "/kg" in raw: p, u = 39.0, "per_kg"
                else: p, u = float(re.findall(r"[\d.]+", raw)[0]), "per_piece"
                inch = lambda mm: round(mm / 25.4 * 4) / 4
                size = f"{inch(l['L_mm'])} x {inch(l['W_mm'])}" + (f" x {inch(l['H_mm'])}" if l["H_mm"] and l["type"] != "PARTITION" else "")
                e = {**base, "status": "quoted", "quote_form": "explicit_price", "price": p, "price_unit": u, "vendor_size_text": size + '"',
                     "extraction_confidence": 0.85, "source": {"file": f_, "location": "row", "snippet": f"{size} {p}"}}
                if lid == "L03": e.update(legibility="handwritten_correction", struck_out_value=9.0)
                if lid == "L07": e.update(legibility="blurred", extraction_confidence=0.3)
                lqs.append(e)
            elif v == "V5":
                f_, ln = find(c, "42/kg")
                if "kg" in raw:
                    rate = 42.0 if l["ply"] == 5 else 38.0
                    lqs.append({**base, "status": "quoted", "quote_form": "per_kg_rate", "price": rate, "price_unit": "per_kg", "extraction_confidence": 0.9, "source": {"file": f_, "location": "L3", "snippet": ln.split("] ", 1)[-1]}})
                else:
                    amb = "May be covered by the 3/5-ply kg rate instead" if l["ply"] in (3, 5) else None
                    lqs.append({**base, "status": "unclear" if amb else "quoted", "quote_form": "same_as_last_year", "price": None, "price_unit": "none", "ambiguity": amb,
                                "source": {"file": f_, "location": "L3", "snippet": "rest same as last year"}})
        com = {
          "V1": {"currency": "INR", "price_basis": "delivered", "freight": {"type": "conditional_free", "conditions_text": "free for full truck load orders (min 3 MT per drop)"}, "payment_days": 30, "validity_days": 30,
                 "other_charges": [{"description": "Printing plates / dies one-time Rs 4,500 per design", "amount_inr": 4500, "basis": "per design"}], "discounts": []},
          "V2": {"currency": "USD", "price_basis": "ex_works", "freight": {"type": "extra_amount_given", "amount_inr": 14500, "per": "truck", "truck_payload_kg": 7000}, "payment_days": 45, "validity_days": 90,
                 "discounts": [{"description": "3.5% volume rebate", "pct": 3.5, "conditional": True, "condition": "annual off-take > USD 350,000"}], "other_charges": [{"description": "Tooling at cost"}]},
          "V3": {"currency": "INR", "price_basis": "delivered", "freight": {"type": "included"}, "payment_days": 60, "validity_days": 180, "discounts": [], "other_charges": []},
          "V4": {"currency": "INR", "price_basis": "delivered", "freight": {"type": "included", "conditions_text": "free within Bhiwandi"}, "payment_days": 30, "validity_days": 41, "discounts": [], "other_charges": [{"description": "Printing block / die charges extra as actual"}]},
          "V5": {"currency": "INR", "price_basis": "ex_works", "freight": {"type": "extra_amount_unknown"}, "discounts": [{"description": "2% cash discount", "pct": 2, "conditional": True, "condition": "payment in 7 days"}], "other_charges": []},
        }[v]
        qa = {
          "V1": [("Q01", "yes", None, None, "Yes - ISO 9001:2015, cert attached"), ("Q02", "unknown", 420, 420, "420 MT"), ("Q03", "yes", None, None, "Yes"), ("Q04", "unknown", 7, 7, "7 days"), ("Q06", "unknown", 8, 8, "Max 8%")],
          "V2": [("Q01", "yes", None, None, "Yes. certificate enclosed"), ("Q02", "unknown", 1200, 1200, "1,200 MT"), ("Q03", "yes", None, None, "Yes"), ("Q04", "unknown", 10, 10, "10 days"), ("Q06", "unknown", 8, 8, "8%")],
          "V3": [("Q01", "yes", None, None, "ISO 9001:2015 certified; copy on request"), ("Q02", "unknown", 350, 350, "350 MT"), ("Q03", "yes", None, None, "Yes"), ("Q04", "unknown", 8, 10, "8-10 days"), ("Q06", "unknown", 8.5, 8.5, "8.5% max")],
          "V4": [],
          "V5": [("Q01", "unknown", None, None, "Docs same as last year"), ("Q04", "unknown", 7, 7, "usual 7 days"), ("Q06", "unknown", None, 10, "below 10%")],
        }[v]
        qs = [{"q_id": q, "answered": True, "yes_no": yn, "numeric_value": nv, "numeric_upper_bound": ub, "answer_text": t} for q, yn, nv, ub, t in qa]
        docs = {"V1": [{"file": "SCI_ISO_9001_2015_Cert.pdf", "doc_type": "iso_certificate", "standard": "ISO 9001:2015", "certificate_no": "BCI/QMS/IN/22-8190", "expiry_date": "2028-02-13"}],
                "V2": [{"file": "PFS_ISO9001_Certificate.pdf", "doc_type": "iso_certificate", "standard": "ISO 9001:2015", "certificate_no": "BCI/QMS/IN/18-4471", "expiry_date": "2026-03-31"}],
                "V3": [{"file": "Deccan_BCT_Test_Report_DCB-QC-BCT-2609.pdf", "doc_type": "test_report", "key_findings": ["BF20 bursting below spec"]}], "V4": [], "V5": []}[v]
        out[v] = {"vendor_id": v, "files": list(c), "run_at": "fixture", "corpus": c, "kinds": kinds,
                  "result": {"commercial": com, "line_quotes": lqs, "questionnaire": qs, "documents": docs,
                             "unmatched_vendor_items": [{"text": "Pizza Box 10\"", "price": 6.8}] if v == "V4" else []}}
    return rfx, out
