"""Deterministic normalisation, validation, confidence and quality-gate evaluation.
Input : RFx + per-vendor AI extractions + buyer overrides + settings + buyer history (ERP, FX).
Output: analysis tables (pandas) written to SQLite for the UI and the analyst agent.
Principle: the LLM reads; this module does ALL arithmetic, and records HOW every number was derived."""
import re, statistics, time
from datetime import date
import pandas as pd
from . import store, readers
from .rfx import load_history, fx_rate, roll_length_m
from .config import DEFAULTS, CONF_HIGH, CONF_MED

SEV_ORDER = {"critical": 0, "review": 1, "info": 2}

def settings():
    s = dict(DEFAULTS); s.update(store.get("settings", {}) or {}); return s

def overrides():
    return store.get("overrides", {}) or {}

def gate_overrides():
    return store.get("gate_overrides", {}) or {}

def norm_id(x, prefix):
    """'1', 'L1', 'item 1' -> 'L01'; 'Q1' -> 'Q01'."""
    m = re.search(r"(\d+)", str(x or ""))
    return f"{prefix}{int(m.group(1)):02d}" if m else str(x)

def conf_label(c):
    return "high" if c >= CONF_HIGH else ("medium" if c >= CONF_MED else "low")

def _num(x):
    try:
        return None if x is None or x == "" else float(x)
    except Exception:
        return None

def _parse_date(s):
    if not s: return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d-%b-%Y", "%d/%m/%Y", "%d.%m.%Y", "%B %d, %Y", "%d %b %Y"):
        try:
            import datetime as dt; return dt.datetime.strptime(s, fmt).date()
        except Exception:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None

# ------------------------------------------------------------------ dimension sanity check
def dims_check(line, size_text):
    """Returns (ok|None, note). Detects inch sizes and checks them against the RFx mm spec."""
    if not size_text:
        return None, None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)(?:\s*[xX×*]\s*(\d+(?:\.\d+)?))?", size_text.replace(",", ""))
    if m:
        nums = [float(g) for g in m.groups() if g]
    else:
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", size_text)] if ('"' in size_text or "width" in size_text.lower() or "mm" in size_text.lower()) else []
    want = [line["L_mm"], line["W_mm"]] + ([line["H_mm"]] if line.get("H_mm") else [])
    if line["type"] == "ROLL":
        want = [line["L_mm"]]
    if len(nums) < len(want) or not want:
        return None, None
    got = nums[:len(want)]
    inches = ('"' in size_text or "inch" in size_text.lower() or all(g < 60 for g in got)) and max(want) > 100
    conv = [g * 25.4 for g in got] if inches else got
    ok = all(abs(c - w) <= max(10, 0.04 * w) for c, w in zip(conv, want))
    unit = "inch" if inches else "mm"
    return ok, f"vendor size '{size_text}' ({unit}) -> {' x '.join(f'{c:.0f}' for c in conv)} mm vs RFx {' x '.join(str(int(w)) for w in want)} mm"

# ------------------------------------------------------------------ main builder
def build(rfx, extractions: dict):
    s = settings(); ov = overrides(); gov = gate_overrides()
    history = load_history()
    due = rfx["due"]
    lines = {l["line_id"]: l for l in rfx["line_items"]}
    vendors = {v["vendor_id"]: v for v in rfx["invited_vendors"]}
    Q, F, T, A, D, U, VROWS, QA = [], [], [], [], [], [], [], []

    def flag(v, lid, sev, code, msg):
        F.append({"vendor_id": v, "line_id": lid, "severity": sev, "code": code, "message": msg,
                  "resolved": 0})

    for vid, rec in extractions.items():
        ex = rec["result"]; com = ex.get("commercial", {}) or {}
        corpus_all = "\n".join(rec.get("corpus", {}).values())
        kinds = rec.get("kinds", {})
        vccy = (com.get("currency") or "INR").upper()
        fr = com.get("freight", {}) or {}
        ftype = fr.get("type", "unclear")
        basis = com.get("price_basis", "unclear")
        if ex.get("_truncated"):
            flag(vid, None, "critical", "truncated", "Extraction output was truncated - re-run extraction before relying on this vendor.")
        # ---- vendor-level commercial flags
        if basis == "ex_works" and ftype in ("included",):
            ftype = "unclear"
        if ftype == "extra_amount_unknown" or (basis == "ex_works" and ftype == "unclear"):
            flag(vid, None, "review", "freight_unknown", f"Freight is extra but no amount given - landed cost uses an ESTIMATE of Rs {s['freight_estimate_inr_per_kg']}/kg. Ask vendor.")
        if ftype == "conditional_free":
            flag(vid, None, "info", "freight_conditional", f"Freight free only on condition: {fr.get('conditions_text')}")
        for dsc in com.get("discounts", []) or []:
            if dsc.get("conditional"):
                flag(vid, None, "info", "conditional_discount", f"Conditional discount NOT applied to prices: {dsc.get('description')} ({dsc.get('condition')})")
        for ch in com.get("other_charges", []) or []:
            flag(vid, None, "review", "extra_charge", f"Additional charge not in unit prices: {ch.get('description')}" + (f" (Rs {ch['amount_inr']:,.0f} {ch.get('basis') or ''})" if ch.get("amount_inr") else ""))
        for dv in com.get("other_deviations", []) or []:
            flag(vid, None, "info", "deviation", dv)
        if vccy != "INR":
            r, d, src = fx_rate(vccy, due)
            if r is None:
                flag(vid, None, "critical", "fx_missing", f"No FX rate for {vccy} - prices cannot be compared.")
            else:
                flag(vid, None, "info", "fx", f"Prices in {vccy}; converted at {r} INR/{vccy} ({d}, {src}).")
        # ---- terms table
        pay_req = 45; val_req = rfx.get("price_validity_required_days") or 180
        pdays, vdays = _num(com.get("payment_days")), _num(com.get("validity_days"))
        def dev(val, req, higher_better=True):
            if val is None: return "unknown"
            if val == req: return "as requested"
            return "better" if (val > req) == higher_better else "worse"
        T += [
            {"vendor_id": vid, "term": "Price basis", "value": basis, "rfx_requirement": "delivered", "deviation": "as requested" if basis == "delivered" else ("worse" if basis == "ex_works" else "unknown")},
            {"vendor_id": vid, "term": "Freight", "value": f"{ftype}" + (f": Rs {fr.get('amount_inr'):,.0f} per {fr.get('per')}" if fr.get("amount_inr") else "") + (f" ({fr.get('conditions_text')})" if fr.get("conditions_text") else ""), "rfx_requirement": "included", "deviation": "as requested" if ftype == "included" else ("worse" if ftype.startswith("extra") else "unknown")},
            {"vendor_id": vid, "term": "Currency", "value": vccy, "rfx_requirement": "INR", "deviation": "as requested" if vccy == "INR" else "worse"},
            {"vendor_id": vid, "term": "Payment (days)", "value": com.get("payment_text") or (str(pdays) if pdays else None), "rfx_requirement": "45 days", "deviation": dev(pdays, pay_req, True)},
            {"vendor_id": vid, "term": "Price validity (days)", "value": com.get("validity_text") or (str(vdays) if vdays else None), "rfx_requirement": f"{val_req} days", "deviation": dev(vdays, val_req, True)},
            {"vendor_id": vid, "term": "Price variation", "value": com.get("price_variation_clause"), "rfx_requirement": "firm, or index-linked stated", "deviation": "unknown" if not com.get("price_variation_clause") else "note"},
            {"vendor_id": vid, "term": "Lead time", "value": com.get("delivery_lead_time_text"), "rfx_requirement": "<= 10 days", "deviation": "note"},
            {"vendor_id": vid, "term": "GST", "value": com.get("gst_text"), "rfx_requirement": "excluded from price", "deviation": "note"},
        ]
        for dsc in com.get("discounts", []) or []:
            T.append({"vendor_id": vid, "term": "Discount/rebate", "value": dsc.get("description") + (f" [condition: {dsc.get('condition')}]" if dsc.get("conditional") else ""), "rfx_requirement": "-", "deviation": "better" if not dsc.get("conditional") else "conditional"})
        for ch in com.get("other_charges", []) or []:
            T.append({"vendor_id": vid, "term": "Extra charge", "value": ch.get("description"), "rfx_requirement": "all-inclusive", "deviation": "worse"})

        uncond_disc = sum(_num(d.get("pct")) or 0 for d in (com.get("discounts") or []) if not d.get("conditional"))
        lq_by = {}
        for lq in ex.get("line_quotes", []) or []:
            lq_by.setdefault(norm_id(lq.get("rfx_line_id"), "L"), lq)
        for u in ex.get("unmatched_vendor_items", []) or []:
            U.append({"vendor_id": vid, "text": u.get("text"), "price": u.get("price"), "reason": u.get("reason")})
            flag(vid, None, "info", "unmatched_item", f"Vendor item not in RFx (ignored): {u.get('text')}")

        rows = {}
        for lid, line in lines.items():
            lq = lq_by.get(lid)
            if lq is None:
                lq = {"rfx_line_id": lid, "status": "not_quoted", "quote_form": "none", "price_unit": "none",
                      "match_confidence": 0, "extraction_confidence": 0, "legibility": "not_applicable"}
                flag(vid, lid, "review", "extractor_omitted", "Extractor returned no entry for this line - verify manually.")
            src = lq.get("source") or {}
            fname = src.get("file") or ""
            kind = kinds.get(fname) or (readers.kind_of(fname) if fname else None)
            row = {"vendor_id": vid, "line_id": lid, "status": lq.get("status"), "quote_form": lq.get("quote_form"),
                   "raw_price": _num(lq.get("price")), "raw_unit": lq.get("price_unit_text") or lq.get("price_unit"),
                   "raw_currency": (lq.get("currency") or vccy).upper() if lq.get("price") is not None else None,
                   "vendor_item_text": lq.get("vendor_item_text"), "source_file": fname, "source_location": src.get("location"),
                   "source_snippet": src.get("snippet"), "bbox": ",".join(str(b) for b in (src.get("bbox") or [])) or None,
                   "price_inr_per_uom": None, "freight_inr_per_uom": None, "landed_inr_per_uom": None,
                   "is_estimate": 0, "is_derived": 0, "derivation": [], "confidence_score": float(lq.get("extraction_confidence") or 0),
                   "match_confidence": float(lq.get("match_confidence") or 0), "ambiguity": lq.get("ambiguity"),
                   "notes": lq.get("notes"), "_ref": lq.get("referenced_line_id")}
            c = row["confidence_score"]; der = row["derivation"]
            form = lq.get("quote_form") or "none"; unit = lq.get("price_unit") or "none"
            price = row["raw_price"]; ccy = row["raw_currency"] or vccy
            if lq.get("status") in ("not_quoted", "declined") or form == "none":
                row["status"] = lq.get("status") if lq.get("status") in ("not_quoted", "declined") else "not_quoted"
                rows[lid] = row; continue

            native = None
            if form == "same_as_last_year":
                h = history.get(str(line.get("material_code")))
                if not h:
                    row["status"] = "unresolvable"
                    flag(vid, lid, "critical", "no_history", f"Vendor says 'same as last year' but there is no last-year price for {line['material_code']} (new item). Must ask vendor.")
                    rows[lid] = row; continue
                native = h["price"] / (h["per"] or 1); ccy = h.get("currency") or "INR"
                der.append(f"'same as last year' -> ERP PO {h['po']} price Rs {h['price']} ({h['source']}, {h['inco']})")
                if ftype.startswith("extra") and h.get("inco") and "DDP" in str(h["inco"]).upper():
                    flag(vid, lid, "review", "history_basis_conflict", f"Last year's price was {h['inco']} (freight included) but vendor now says freight extra - is freight double-counted?")
                c = min(max(c, 0.7), 0.75); row["is_estimate"] = 1
            elif form == "same_as_other_line":
                row["status"] = "pending_ref"; rows[lid] = row; continue
            else:
                if price is None:
                    row["status"] = "unclear"
                    flag(vid, lid, "review", "no_price", "Vendor appears to quote this line but no readable price was extracted.")
                    rows[lid] = row; continue
                if form == "per_kg_rate" and unit in ("none", "other", "per_piece"):
                    unit = "per_kg"
                w = line.get("est_weight_kg"); rl = roll_length_m(line)
                if unit in ("per_piece", "per_set", "per_roll"):
                    native = price
                    if unit == "per_roll" and line["type"] != "ROLL" or (unit == "per_set" and line["uom"] != "set"):
                        flag(vid, lid, "info", "unit_label", f"Vendor unit '{row['raw_unit']}' vs RFx UoM '{line['uom']}' - treated as 1:1.")
                elif unit == "per_100":
                    native = price / 100; der.append(f"{price} per 100 -> /100")
                elif unit == "per_1000":
                    native = price / 1000; der.append(f"{price} per 1000 -> /1000")
                elif unit == "per_kg":
                    if not w:
                        row["status"] = "unclear"; flag(vid, lid, "critical", "no_weight", "Per-kg price but no spec weight to convert."); rows[lid] = row; continue
                    native = price * w; der.append(f"{price}/kg x {w} kg (RFx spec weight) = {native:.2f}")
                    row["is_derived"] = 1; c = min(c, 0.75)
                    flag(vid, lid, "info", "per_kg_derived", f"Per-kg rate converted with NHPC spec weight {w} kg; vendor will bill actual weight.")
                elif unit == "per_running_metre":
                    if not rl:
                        row["status"] = "unclear"; flag(vid, lid, "critical", "unit_mismatch", "Per running metre on a non-roll item."); rows[lid] = row; continue
                    native = price * rl; der.append(f"{price}/m x {rl:.0f} m per roll")
                else:
                    row["status"] = "unclear"
                    flag(vid, lid, "review", "unknown_unit", f"Unrecognised price unit '{row['raw_unit']}' - cannot normalise.")
                    rows[lid] = row; continue
            # currency
            if ccy and ccy != "INR":
                r, d, _src = fx_rate(ccy, due)
                if r is None:
                    row["status"] = "unclear"; rows[lid] = row; continue
                der.append(f"{ccy} {native:.4f} x {r} = Rs {native * r:.2f}"); native *= r
            if uncond_disc:
                native *= (1 - uncond_disc / 100); der.append(f"less unconditional discount {uncond_disc}%")
            row["price_inr_per_uom"] = round(native, 4)
            # freight
            w = line.get("est_weight_kg") or 0
            if ftype in ("included", "conditional_free") or (basis == "delivered" and ftype == "unclear"):
                fpu = 0.0
            elif ftype == "extra_amount_given" and fr.get("amount_inr"):
                per = fr.get("per")
                if per == "truck":
                    payload = _num(fr.get("truck_payload_kg"))
                    if payload:
                        fpu = fr["amount_inr"] / payload * w; der.append(f"freight Rs {fr['amount_inr']:,.0f}/{payload:,.0f} kg truck x {w} kg")
                    else:
                        fpu = s["freight_estimate_inr_per_kg"] * w; row["is_estimate"] = 1; der.append("freight per truck but payload unknown -> estimate")
                elif per == "kg":
                    fpu = fr["amount_inr"] * w; der.append(f"freight Rs {fr['amount_inr']}/kg x {w} kg")
                elif per == "unit":
                    fpu = fr["amount_inr"]; der.append("freight per unit")
                else:
                    fpu = s["freight_estimate_inr_per_kg"] * w; row["is_estimate"] = 1; der.append("freight basis unclear -> estimate")
            else:
                fpu = s["freight_estimate_inr_per_kg"] * w; row["is_estimate"] = 1
                der.append(f"freight ESTIMATED at Rs {s['freight_estimate_inr_per_kg']}/kg x {w} kg")
            row["freight_inr_per_uom"] = round(fpu, 4)
            row["landed_inr_per_uom"] = round(native + fpu, 4)
            row["status"] = "quoted" if lq.get("status") != "unclear" else "unclear"

            # ---------------- trust checks
            if kind == "image":
                c = min(c, 0.8); der.append("read from photo (visual check recommended)")
            elif form in ("explicit_price", "per_kg_rate"):
                if not readers.verify_snippet(src.get("snippet"), corpus_all):
                    c = min(c, 0.4); flag(vid, lid, "review", "citation_unverified", f"Cited text not found in the vendor's file: '{(src.get('snippet') or '')[:80]}'")
                elif not readers.number_in_snippet(price, src.get("snippet")):
                    c = min(c, 0.5); flag(vid, lid, "review", "number_not_in_citation", f"Price {price} does not appear in the cited text.")
            leg = lq.get("legibility")
            if leg == "handwritten_correction":
                c = min(c, 0.6); flag(vid, lid, "review", "handwritten", f"Handwritten correction: printed {lq.get('struck_out_value')} struck out, pen value {price} used.")
            elif leg == "blurred":
                c = min(c, 0.35); flag(vid, lid, "review", "illegible", "Price is blurred/smudged in the source - do not rely without confirmation.")
            elif leg == "partially_legible":
                c = min(c, 0.5); flag(vid, lid, "review", "partly_legible", "Price only partially legible.")
            if row["match_confidence"] < 0.8:
                c = min(c, row["match_confidence"]); flag(vid, lid, "review", "weak_match", f"Uncertain line match ({row['match_confidence']:.0%}): {lq.get('match_reason')}")
            ok, note = dims_check(line, lq.get("vendor_size_text"))
            if ok is False:
                c = min(c, 0.5); flag(vid, lid, "review", "size_mismatch", "Size does not match spec: " + note)
            elif ok and note and "inch" in note:
                der.append("size check OK: " + note)
            if lq.get("status") == "unclear" or lq.get("ambiguity"):
                c = min(c, 0.6); flag(vid, lid, "review", "ambiguous", f"Ambiguous: {lq.get('ambiguity') or 'vendor statement may not cover this line'}")
            if lq.get("spec_deviation"):
                flag(vid, lid, "review", "spec_deviation", f"Offer deviates from spec: {lq['spec_deviation']}")
            vq, va = _num(lq.get("vendor_qty")), _num(lq.get("vendor_amount"))
            if vq and va and price:
                div = {"per_100": 100, "per_1000": 1000}.get(unit, 1)
                exp = vq * price / div
                if abs(exp - va) > max(1.0, 0.01 * va):
                    flag(vid, lid, "review", "arithmetic", f"Vendor amount Rs {va:,.0f} != qty {vq:,.0f} x rate {price} {('per '+str(div)) if div>1 else ''} = Rs {exp:,.0f}. Rate used; confirm with vendor.")
                    c = min(c, 0.7)
            row["confidence_score"] = round(c, 2)
            rows[lid] = row
            for alt in lq.get("alternate_offers", []) or []:
                ap = _num(alt.get("price"))
                A.append({"vendor_id": vid, "line_id": lid, "description": alt.get("description"), "deviation": alt.get("deviation"),
                          "raw_price": ap, "price_inr_per_uom": round(ap * (fx_rate(ccy, due)[0] or 1), 2) if ap else None,
                          "landed_inr_per_uom": round(ap * (fx_rate(ccy, due)[0] or 1) + fpu, 2) if ap else None})
                flag(vid, lid, "info", "alternate_offer", f"Alternate offer: {alt.get('description')} @ {ap} ({alt.get('deviation') or ''}) - NOT used as the compliant price.")
        # resolve cross references
        for lid, row in rows.items():
            if row["status"] == "pending_ref":
                ref = rows.get(norm_id(row["_ref"], "L") if row["_ref"] else "")
                if ref and ref.get("landed_inr_per_uom") is not None:
                    for k in ("price_inr_per_uom", "freight_inr_per_uom", "landed_inr_per_uom", "is_estimate", "is_derived"):
                        row[k] = ref[k]
                    row["raw_price"] = ref["raw_price"]; row["raw_unit"] = ref["raw_unit"]; row["raw_currency"] = ref["raw_currency"]
                    row["derivation"] = [f"'same as {row['_ref']}' -> {ref['raw_price']} (resolved cross-reference)"] + ref["derivation"]
                    row["status"] = "quoted"; row["confidence_score"] = min(ref["confidence_score"], row["confidence_score"] or 1, 0.8)
                    flag(vid, lid, "review", "cross_reference", f"Price given by reference ('same as {row['_ref']}'). Line specs differ - confirm intent.")
                else:
                    row["status"] = "unresolvable"; flag(vid, lid, "critical", "bad_reference", f"Refers to {row['_ref']} which has no price.")
        for row in rows.values():
            row.pop("_ref", None)
        Q.extend(rows.values())

        # ---- documents
        for d in ex.get("documents", []) or []:
            exp = _parse_date(d.get("expiry_date")); duedt = _parse_date(due)
            valid = None if not exp else int(exp >= duedt)
            D.append({"vendor_id": vid, "file": d.get("file"), "doc_type": d.get("doc_type"), "holder_name": d.get("holder_name"),
                      "standard": d.get("standard"), "certificate_no": d.get("certificate_no"), "issue_date": d.get("issue_date"),
                      "expiry_date": exp.isoformat() if exp else d.get("expiry_date"), "valid_on_due_date": valid,
                      "key_findings": "; ".join(d.get("key_findings") or [])})
            if d.get("doc_type") == "iso_certificate" and valid == 0:
                flag(vid, None, "critical", "cert_expired", f"{d.get('standard') or 'Certificate'} {d.get('certificate_no') or ''} EXPIRED on {exp} (bid due {due}) - file {d.get('file')}.")

        # ---- questionnaire / gate
        ans_by = {norm_id(a.get("q_id"), "Q"): a for a in ex.get("questionnaire", []) or []}
        results = []
        for q in rfx["questionnaire"]:
            a = ans_by.get(q["id"], {"answered": False, "yes_no": "unknown"})
            rule = q.get("rule") or {"type": "info"}
            res, why = "info", ""
            if rule["type"] == "cert_valid":
                stdkey = "9001" if "9001" in str(rule.get("standard", "")) else ""
                certs = [d for d in D if d["vendor_id"] == vid and d["doc_type"] == "iso_certificate"
                         and (stdkey in (d.get("standard") or "") or not d.get("standard"))]
                if certs:
                    good = [d for d in certs if d["valid_on_due_date"] == 1]
                    if good:
                        res, why = "pass", f"Certificate {good[0]['certificate_no']} valid to {good[0]['expiry_date']} ({good[0]['file']})"
                    else:
                        res, why = "fail", f"Vendor answer: '{a.get('answer_text') or a.get('yes_no')}', but attached certificate {certs[0]['certificate_no']} expired {certs[0]['expiry_date']}"
                elif a.get("yes_no") == "yes":
                    res, why = "conditional", f"Claimed ('{(a.get('answer_text') or '')[:90]}') but no certificate attached"
                elif a.get("yes_no") == "no":
                    res, why = "fail", "Vendor says no"
                else:
                    res, why = "unknown", "Not answered / no evidence"
            elif rule["type"] in ("min", "max"):
                val = _num(a.get("numeric_value")) if rule["type"] == "min" else (_num(a.get("numeric_upper_bound")) if a.get("numeric_upper_bound") is not None else _num(a.get("numeric_value")))
                if val is None:
                    res, why = "unknown", "No numeric answer"
                else:
                    okv = val >= rule["value"] if rule["type"] == "min" else val <= rule["value"]
                    res = "pass" if okv else "fail"
                    why = f"{a.get('answer_text') or val} -> {val:g} {'>=' if rule['type']=='min' else '<='} {rule['value']:g}? {'yes' if okv else 'no'}"
            elif rule["type"] == "yes":
                res = {"yes": "pass", "no": "fail"}.get(a.get("yes_no"), "unknown"); why = a.get("answer_text") or "Not answered"
            else:
                why = a.get("answer_text") or ("Not answered" if not a.get("answered") else "")
            results.append({"vendor_id": vid, "q_id": q["id"], "question": q["question"], "knock_out": int(bool(q.get("knock_out"))),
                            "criterion": q.get("criterion"), "answer_text": a.get("answer_text"), "result": res, "reason": why,
                            "source_location": (a.get("source") or {}).get("location")})
        Q_ = results
        ko = [r for r in Q_ if r["knock_out"]]
        if any(r["result"] == "fail" for r in ko): status = "NOT CLEARED"
        elif all(r["result"] == "unknown" for r in ko): status = "UNKNOWN"
        elif any(r["result"] == "unknown" for r in ko): status = "INCOMPLETE"
        elif any(r["result"] == "conditional" for r in ko): status = "CONDITIONAL"
        else: status = "CLEARED"
        reasons = "; ".join(f"{r['q_id']} {r['result']}: {r['reason']}" for r in ko if r["result"] != "pass")
        if vid in gov:
            g = gov[vid]; reasons = f"BUYER OVERRIDE ({g.get('reason')}) from {status}. " + reasons; status = g["status"]
        VROWS.append({"vendor_id": vid, "vendor_name": vendors.get(vid, {}).get("name", vid), "city": vendors.get(vid, {}).get("city"),
                      "gate_status": status, "gate_reasons": reasons or "All knock-out criteria met",
                      "currency": vccy, "price_basis": basis, "freight_type": ftype,
                      "payment_days": pdays, "validity_days": vdays, "files": ", ".join(rec.get("files", [])), "extracted_at": rec.get("run_at")})
        QA.extend(Q_)

    qa = QA
    qdf = pd.DataFrame(Q)
    fdf = pd.DataFrame(F, columns=["vendor_id", "line_id", "severity", "code", "message", "resolved"])
    # ---- outliers (vs median of other vendors on landed price)
    if not qdf.empty:
        for lid, g in qdf[qdf["landed_inr_per_uom"].notna()].groupby("line_id"):
            for i, r in g.iterrows():
                others = g[g["vendor_id"] != r["vendor_id"]]["landed_inr_per_uom"].tolist()
                if len(others) >= 2:
                    med = statistics.median(others); devp = (r["landed_inr_per_uom"] - med) / med * 100
                    if abs(devp) > s["outlier_threshold_pct"]:
                        fdf.loc[len(fdf)] = [r["vendor_id"], lid, "review", "outlier", f"Landed Rs {r['landed_inr_per_uom']:.2f} is {devp:+.0f}% vs median of other vendors (Rs {med:.2f}). Possible unit or reading error.", 0]
                        qdf.loc[i, "confidence_score"] = min(qdf.loc[i, "confidence_score"], 0.6)
        # ---- apply buyer overrides
        qdf["review_status"] = "auto"; qdf["override_note"] = None
        for key, o in ov.items():
            v, lid = key.split("|")
            m = (qdf["vendor_id"] == v) & (qdf["line_id"] == lid)
            if not m.any(): continue
            i = qdf[m].index[0]
            if o["action"] == "confirm":
                qdf.loc[i, "review_status"] = "confirmed"; qdf.loc[i, "confidence_score"] = 1.0
            elif o["action"] == "set_price":
                p = float(o["price_inr_per_uom"]); f_ = float(o.get("freight_inr_per_uom") if o.get("freight_inr_per_uom") is not None else (qdf.loc[i, "freight_inr_per_uom"] or 0))
                qdf.loc[i, ["price_inr_per_uom", "freight_inr_per_uom", "landed_inr_per_uom"]] = [p, f_, p + f_]
                qdf.loc[i, "status"] = "quoted"; qdf.loc[i, "review_status"] = "overridden"; qdf.loc[i, "confidence_score"] = 1.0
                qdf.loc[i, "is_estimate"] = 0
            elif o["action"] == "exclude":
                qdf.loc[i, "review_status"] = "excluded"
            qdf.loc[i, "override_note"] = f"{o['action']} by {o.get('by','buyer')} {o.get('ts','')}: {o.get('reason','')}"
            fdf.loc[(fdf["vendor_id"] == v) & (fdf["line_id"] == lid), "resolved"] = 1
        # review status for the rest
        open_rev = fdf[(fdf["resolved"] == 0) & (fdf["severity"].isin(["review", "critical"])) & fdf["line_id"].notna()]
        need = set(zip(open_rev["vendor_id"], open_rev["line_id"]))
        for i, r in qdf.iterrows():
            if r["review_status"] == "auto" and r["status"] not in ("not_quoted", "declined") and ((r["vendor_id"], r["line_id"]) in need or r["confidence_score"] < CONF_MED):
                qdf.loc[i, "review_status"] = "needs_review"
        qdf["confidence"] = qdf["confidence_score"].apply(conf_label)
        qdf.loc[qdf["review_status"].isin(["confirmed", "overridden"]), "confidence"] = "confirmed"
        qdf.loc[qdf["status"].isin(["not_quoted", "declined", "unresolvable"]), "confidence"] = None
        qdf["derivation"] = qdf["derivation"].apply(lambda d: " | ".join(d) if isinstance(d, list) else d)
        qdf = qdf.merge(pd.DataFrame([{"line_id": k, "annual_qty": v["annual_qty"], "uom": v["uom"], "description": v["description"]} for k, v in lines.items()]), on="line_id")
        qdf["annual_value_inr"] = (qdf["landed_inr_per_uom"] * qdf["annual_qty"]).round(0)
        # usable for award = priced, not excluded
        qdf["usable"] = (qdf["landed_inr_per_uom"].notna() & (qdf["review_status"] != "excluded")).astype(int)
        fl = fdf[fdf["line_id"].notna()].groupby(["vendor_id", "line_id"])["message"].apply(lambda x: " || ".join(x)).reset_index().rename(columns={"message": "flags"})
        qdf = qdf.merge(fl, on=["vendor_id", "line_id"], how="left")
    vdf = pd.DataFrame(VROWS)
    if not vdf.empty and not qdf.empty:
        agg = qdf.groupby("vendor_id").agg(lines_priced=("usable", "sum"), needs_review=("review_status", lambda x: int((x == "needs_review").sum())),
                                           annual_value_priced_lines=("annual_value_inr", "sum")).reset_index()
        vdf = vdf.merge(agg, on="vendor_id", how="left"); vdf["lines_total"] = len(lines)
    ldf = pd.DataFrame([{"line_id": k, "material_code": v["material_code"], "description": v["description"], "type": v["type"], "ply": v["ply"],
                         "uom": v["uom"], "annual_qty": v["annual_qty"], "est_weight_kg": v.get("est_weight_kg"),
                         "last_year_price_inr": (history.get(str(v["material_code"])) or {}).get("price"),
                         "last_year_vendor": (history.get(str(v["material_code"])) or {}).get("vendor_name")} for k, v in lines.items()])
    tables = {"lines": ldf, "vendors": vdf, "quotes": qdf, "flags": fdf, "questionnaire": pd.DataFrame(qa), "terms": pd.DataFrame(T),
              "alternates": pd.DataFrame(A, columns=["vendor_id", "line_id", "description", "deviation", "raw_price", "price_inr_per_uom", "landed_inr_per_uom"]),
              "documents": pd.DataFrame(D, columns=["vendor_id", "file", "doc_type", "holder_name", "standard", "certificate_no", "issue_date", "expiry_date", "valid_on_due_date", "key_findings"]),
              "unmatched_items": pd.DataFrame(U, columns=["vendor_id", "text", "price", "reason"])}
    return tables

def rebuild():
    """Recompute every analysis table from current state and persist. Returns tables."""
    rfx = store.get("rfx")
    if not rfx: return {}
    ex = {k.split(":", 1)[1]: store.get(k) for k in store.keys("extraction:")}
    if not ex: return {}
    tables = build(rfx, ex)
    store.write_tables(tables)
    return tables
