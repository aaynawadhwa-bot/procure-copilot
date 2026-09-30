"""RFx domain model: loading, spec -> weight model, questionnaire rules, buyer history, FX."""
import json, re
from pathlib import Path
import openpyxl
from . import store
from .config import DEMO_RFX, DEMO_HISTORY

FLUTE_TAKEUP = 1.45
TYPES = ["RSC", "HSC", "MAILER", "TRAY", "PARTITION", "PAD", "ROLL", "INSERT", "BIN"]

# ---------------------------------------------------------------- weight model
def combined_gsm(paper):
    return sum(p * (FLUTE_TAKEUP if i % 2 == 1 else 1) for i, p in enumerate(paper))

def board_area_m2(t, L, W, H, cells=None):
    if t == "RSC": a = (2*L + 2*W + 35) * (W + H)
    elif t == "HSC": a = (2*L + 2*W + 35) * (H + W/2)
    elif t == "MAILER": a = (L + 4*H + 40) * (2*W + 3*H + 30)
    elif t == "TRAY": a = (L + 2*H + 30) * (W + 2*H + 30)
    elif t == "PARTITION":
        r, c = cells or (2, 2); a = ((r-1)*L + (c-1)*W) * H
    elif t == "PAD": a = L * W
    elif t == "INSERT": a = 0.6 * (2*L + 2*W) * H
    elif t == "BIN": a = (2*L + 2*W + 35) * (W + H) * 1.25
    elif t == "ROLL": a = L * W          # width x length
    else: a = (2*L + 2*W + 35) * (W + H)
    return a / 1e6

def est_weight_kg(line):
    return round(board_area_m2(line["type"], line["L_mm"], line["W_mm"], line.get("H_mm", 0) or 0, line.get("cells"))
                 * combined_gsm(line["paper_gsm"]) / 1000, 4)

def roll_length_m(line):
    return (line["W_mm"] / 1000.0) if line["type"] == "ROLL" else None

def complete_line(line):
    """Fill derived fields for lines authored by the co-pilot."""
    line.setdefault("material_code", "NEW")
    line.setdefault("print", "Plain"); line.setdefault("note", ""); line.setdefault("cells", None)
    line.setdefault("H_mm", 0)
    if line.get("paper_gsm"):
        line["combined_gsm"] = round(combined_gsm(line["paper_gsm"]), 1)
        line["board_area_m2"] = round(board_area_m2(line["type"], line["L_mm"], line["W_mm"], line["H_mm"], line.get("cells")), 4)
        line["est_weight_kg"] = est_weight_kg(line)
    return line

# ---------------------------------------------------------------- questionnaire rules
def parse_rule(q):
    """Turn the human criterion into a machine rule. Explicit 'rule' wins if present."""
    if q.get("rule"):
        return q["rule"]
    crit = (q.get("criterion") or "").lower(); text = (q.get("question") or "").lower()
    if not q.get("knock_out"):
        return {"type": "info"}
    if "certificate" in crit or "iso" in text:
        std = "ISO 9001" if "9001" in text + crit else "certificate"
        return {"type": "cert_valid", "standard": std}
    m = re.search(r"(>=|<=|≥|≤)\s*([\d.]+)", crit)
    if m:
        op = ">=" if m.group(1) in (">=", "≥") else "<="
        return {"type": "min" if op == ">=" else "max", "value": float(m.group(2))}
    if "yes" in crit:
        return {"type": "yes"}
    return {"type": "manual"}

# ---------------------------------------------------------------- persistence
def load_demo_rfx():
    rfx = json.loads(Path(DEMO_RFX).read_text())
    for q in rfx["questionnaire"]:
        q["rule"] = parse_rule(q)
    rfx["line_items"] = [complete_line(l) for l in rfx["line_items"]]
    return rfx

def save_rfx(rfx):
    for q in rfx.get("questionnaire", []):
        q["rule"] = parse_rule(q)
    rfx["line_items"] = [complete_line(l) for l in rfx.get("line_items", [])]
    store.put("rfx", rfx)
    store.log("rfx_saved", {"rfx_no": rfx.get("rfx_no"), "lines": len(rfx.get("line_items", []))}, actor="buyer")

def current_rfx():
    return store.get("rfx")

# ---------------------------------------------------------------- buyer history + FX
def load_history():
    """Last-year PO prices keyed by material code (ERP export). Returns {code: {...}}."""
    out = {}
    for f in Path(DEMO_HISTORY).glob("*.xlsx"):
        ws = openpyxl.load_workbook(f, data_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        hdr = [str(h).strip() if h else "" for h in rows[0]]
        for i, r in enumerate(rows[1:], start=2):
            d = dict(zip(hdr, r))
            code = d.get("Material")
            if not code:
                continue
            out[str(code)] = {"vendor_code": d.get("Vendor"), "vendor_name": d.get("Vendor name"),
                              "price": float(d.get("Net Price") or 0), "per": float(d.get("Per") or 1),
                              "currency": d.get("Currency"), "inco": d.get("Inco"),
                              "valid_to": d.get("Valid to"), "po": d.get("Purchasing Doc."),
                              "source": f"{f.name} row {i}"}
    return out

def load_fx():
    f = Path(DEMO_HISTORY) / "fx_rates.json"
    return json.loads(f.read_text()) if f.exists() else {}

def fx_rate(ccy, date):
    """INR per 1 unit of ccy on date (falls back to nearest earlier date). Returns (rate, date_used, source)."""
    if not ccy or ccy.upper() in ("INR", "RS", "₹"):
        return 1.0, None, None
    fx = load_fx(); table = fx.get(f"{ccy.upper()}INR")
    if not table:
        return None, None, None
    dates = sorted(d for d in table if d <= date) or sorted(table)
    d = dates[-1]
    return float(table[d]), d, fx.get("source", "fx_rates.json")
