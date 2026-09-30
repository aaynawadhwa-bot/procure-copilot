"""AI extraction: one Claude call per vendor reads ALL of that vendor's files (quote + attachments) and
maps what the vendor said onto the buyer's RFx lines. The model reads and interprets; it does NOT convert
units or currencies and does NOT do arithmetic - deterministic code does that downstream (normalize.py)."""
import hashlib, json, time
from pathlib import Path
from . import llm, readers, store
from .config import INBOX_DIR, EXTRACTION_PROMPT_VERSION, MODEL

N = ["number", "null"]; S = ["string", "null"]
SOURCE = {"type": "object", "properties": {
    "file": {"type": "string"},
    "location": {"type": "string", "description": "Excel: 'Sheet!A12:H12'; Word: 'P9'; PDF: 'page 1'; email: 'L3'; image: 'row 5 of rate list'"},
    "snippet": {"type": "string", "description": "VERBATIM text copied from the source (for images: transcribe exactly what is visible)"},
    "bbox": {"type": ["array", "null"], "items": {"type": "number"}, "description": "images only: [x0,y0,x1,y1] as fractions 0-1 of the image"}},
    "required": ["file", "location", "snippet"]}

SCHEMA = {"type": "object", "properties": {
  "vendor_name_as_written": {"type": "string"},
  "response_date": S,
  "commercial": {"type": "object", "properties": {
      "currency": {"type": "string", "description": "ISO code of the currency prices are quoted in, e.g. INR, USD"},
      "price_basis": {"type": "string", "enum": ["delivered", "ex_works", "unclear"]},
      "freight": {"type": "object", "properties": {
          "type": {"type": "string", "enum": ["included", "extra_amount_given", "extra_amount_unknown", "conditional_free", "unclear"]},
          "amount_inr": N, "per": {"type": "string", "enum": ["truck", "kg", "unit", "order", "none"]},
          "truck_payload_kg": N, "conditions_text": S, "source": SOURCE}, "required": ["type"]},
      "payment_days": N, "payment_text": S, "validity_days": N, "validity_text": S,
      "price_variation_clause": S, "gst_text": S, "delivery_lead_time_text": S,
      "discounts": {"type": "array", "items": {"type": "object", "properties": {
          "description": {"type": "string"}, "pct": N, "conditional": {"type": "boolean"}, "condition": S, "source": SOURCE},
          "required": ["description", "conditional"]}},
      "other_charges": {"type": "array", "items": {"type": "object", "properties": {
          "description": {"type": "string"}, "amount_inr": N, "basis": S, "source": SOURCE}, "required": ["description"]}},
      "other_deviations": {"type": "array", "items": {"type": "string"}}},
    "required": ["currency", "price_basis", "freight"]},
  "line_quotes": {"type": "array", "description": "EXACTLY ONE entry per RFx line id, in RFx order, including lines the vendor did not quote.",
    "items": {"type": "object", "properties": {
      "rfx_line_id": {"type": "string"},
      "status": {"type": "string", "enum": ["quoted", "not_quoted", "declined", "unclear"]},
      "quote_form": {"type": "string", "enum": ["explicit_price", "per_kg_rate", "same_as_other_line", "same_as_last_year", "none"]},
      "price": {"type": N, "description": "number exactly as written by the vendor, no conversion"},
      "price_unit": {"type": "string", "enum": ["per_piece", "per_set", "per_roll", "per_100", "per_1000", "per_kg", "per_running_metre", "per_bundle", "other", "none"]},
      "price_unit_text": S, "currency": S,
      "referenced_line_id": {"type": S, "description": "for same_as_other_line: the RFx line id whose price applies"},
      "vendor_item_text": S, "vendor_size_text": S,
      "match_confidence": {"type": "number", "description": "0-1: how sure the vendor item is this RFx line"},
      "match_reason": S,
      "spec_deviation": {"type": S, "description": "any difference vs RFx spec in the MAIN offer"},
      "alternate_offers": {"type": "array", "items": {"type": "object", "properties": {
          "description": {"type": "string"}, "price": N, "price_unit": {"type": "string"}, "deviation": S, "source": SOURCE},
          "required": ["description"]}},
      "vendor_qty": N, "vendor_qty_unit": S, "vendor_amount": N,
      "legibility": {"type": "string", "enum": ["clear", "handwritten_correction", "blurred", "partially_legible", "not_applicable"]},
      "struck_out_value": {"type": N, "description": "if a printed value was crossed out and replaced"},
      "extraction_confidence": {"type": "number", "description": "0-1: how sure you are the price you read is exactly what the vendor wrote"},
      "ambiguity": {"type": S, "description": "describe any competing interpretation"},
      "source": SOURCE, "notes": S},
      "required": ["rfx_line_id", "status", "quote_form", "price_unit", "match_confidence", "extraction_confidence", "legibility"]}},
  "unmatched_vendor_items": {"type": "array", "items": {"type": "object", "properties": {
      "text": {"type": "string"}, "price": N, "reason": S}, "required": ["text"]}},
  "questionnaire": {"type": "array", "description": "EXACTLY ONE entry per RFx question id",
    "items": {"type": "object", "properties": {
      "q_id": {"type": "string"}, "answered": {"type": "boolean"}, "answer_text": S,
      "yes_no": {"type": "string", "enum": ["yes", "no", "unknown", "not_applicable"]},
      "numeric_value": N, "numeric_upper_bound": {"type": N, "description": "the WORST case implied, e.g. '8-10 days' -> 10, 'below 10%' -> 10"},
      "evidence_claimed": S, "source": SOURCE}, "required": ["q_id", "answered", "yes_no"]}},
  "documents": {"type": "array", "description": "one entry per attached document (certificates, test reports, etc.)",
    "items": {"type": "object", "properties": {
      "file": {"type": "string"}, "doc_type": {"type": "string", "enum": ["iso_certificate", "test_report", "gst_certificate", "quotation", "other"]},
      "holder_name": S, "standard": S, "certificate_no": S, "issue_date": S,
      "expiry_date": {"type": S, "description": "YYYY-MM-DD exactly as printed on the document"},
      "key_findings": {"type": "array", "items": {"type": "string"}}, "source": SOURCE}, "required": ["file", "doc_type"]}},
  "overall_notes": S},
  "required": ["commercial", "line_quotes", "questionnaire", "documents"]}

def _fix(node):
    """Wrap shorthand property values (a bare type list like ["string","null"]) into {"type": [...]}."""
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            node["properties"] = {k: ({"type": v} if isinstance(v, list) else _fix(v)) for k, v in node["properties"].items()}
        if isinstance(node.get("items"), list):
            node["items"] = {"type": node["items"]}
        for k in ("items",):
            if isinstance(node.get(k), dict): _fix(node[k])
    return node
SCHEMA = _fix(SCHEMA)

SYSTEM = """You are a meticulous procurement data-extraction engine working for a buyer. You read supplier
responses in whatever shape they arrive (spreadsheets in the supplier's own layout, PDFs, Word letters with
prices in prose, phone photos of printed rate cards, one-line emails) and map them onto the buyer's RFx.

Hard rules:
1. NEVER invent or guess a number. If you can't read it, say so (legibility, low extraction_confidence, notes).
2. Copy numbers EXACTLY as written. Do NOT convert units, currencies, per-100 to per-piece, per-kg to per-box,
   inches to mm, or apply discounts. Report the unit the vendor used in price_unit / price_unit_text. Code does the maths.
3. Every price and every questionnaire answer needs a source with a VERBATIM snippet and a precise location.
4. Match vendor items to RFx lines by specification (type, ply, dimensions, print, board), NOT by row order.
   Vendors may use their own names, their own order, inches instead of mm, or no item numbers.
   Put your reasoning in match_reason and be honest in match_confidence.
5. Output exactly one line_quotes entry per RFx line. If the vendor did not quote a line: status not_quoted
   (or declined if they explicitly refused), quote_form none.
6. Cross-references ("item 10 same as item 6") -> quote_form same_as_other_line + referenced_line_id.
   "Same as last year" -> quote_form same_as_last_year (price null). Blanket per-kg rates -> quote_form per_kg_rate
   with the kg rate as price. If it is unclear whether a blanket statement covers a line, set status 'unclear' and
   explain the competing interpretations in 'ambiguity'.
7. Handwritten corrections: report the corrected value as price, the struck value in struck_out_value,
   legibility handwritten_correction. Blurred/smudged digits: legibility blurred and extraction_confidence <= 0.4.
8. If the vendor offers an alternative spec, the main 'price' is for the RFx spec (if given); alternatives go
   in alternate_offers. If only a deviating spec is offered, describe it in spec_deviation.
9. Extract the questionnaire answers from wherever they appear (a sheet, a table, prose, an email line).
   Distinguish what the vendor CLAIMS from what attached documents SHOW. For each attached document, read the
   actual dates and holder printed on it.
10. Capture every commercial condition that changes landed cost or risk: freight terms and amounts, conditional
   rebates, cash discounts, tooling/plate charges, MOQ, validity, payment terms, price-variation clauses.
Be complete. The buyer will put crores of rupees behind this, so precision beats fluency."""

def rfx_brief(rfx):
    lines = [f"{l['line_id']} | code {l['material_code']} | {l['description']} | qty {l['annual_qty']} {l['uom']}"
             + (f" | note: {l['note']}" if l.get('note') else "") for l in rfx["line_items"]]
    qs = [f"{q['id']}: {q['question']} (criterion: {q['criterion']})" for q in rfx["questionnaire"]]
    return (f"RFx {rfx['rfx_no']} - {rfx['title']}\nBuyer price basis requested: {rfx['price_basis']}\n"
            f"Payment requested: {rfx['payment_terms']}. Price validity required: {rfx.get('price_validity_required_days')} days.\n"
            f"Bid due date: {rfx['due']}\n\nRFx LINES (internal dimensions L x W x H in mm):\n" + "\n".join(lines)
            + "\n\nQUESTIONNAIRE:\n" + "\n".join(qs))

def vendor_files(vendor_id):
    d = INBOX_DIR / vendor_id
    return sorted([p for p in d.glob("*") if p.is_file() and not p.name.startswith(".")]) if d.exists() else []

def files_hash(files):
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode()); h.update(Path(f).read_bytes())
    return h.hexdigest()[:20]

def extract_vendor(rfx, vendor, force=False):
    files = vendor_files(vendor["vendor_id"])
    if not files:
        raise ValueError(f"No files received from {vendor['name']}")
    fh = files_hash(files)
    key = f"{EXTRACTION_PROMPT_VERSION}_{vendor['vendor_id']}_{fh}_{hashlib.sha256(rfx_brief(rfx).encode()).hexdigest()[:10]}_{MODEL}"
    if force:
        for c in (llm.CACHE_DIR.glob(f"record_vendor_response_{key}*")):
            c.unlink()
    content = [{"type": "text", "text": rfx_brief(rfx) + f"\n\nSUPPLIER: {vendor['name']} ({vendor.get('city','')}).\n"
                "Below are ALL files received from this supplier. Extract them into the record_vendor_response tool."}]
    for f in files:
        content += readers.content_blocks(f)
    t0 = time.time()
    result = llm.structured(SYSTEM, content, "record_vendor_response",
                            "Record the supplier's complete response mapped onto the RFx.", SCHEMA, cache_key=key, max_tokens=20000)
    rec = {"vendor_id": vendor["vendor_id"], "files": [f.name for f in files], "files_hash": fh, "model": MODEL,
           "prompt_version": EXTRACTION_PROMPT_VERSION, "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "seconds": round(time.time() - t0, 1), "result": result,
           "corpus": {f.name: readers.to_text(f) for f in files},
           "kinds": {f.name: readers.kind_of(f) for f in files}}
    store.put(f"extraction:{vendor['vendor_id']}", rec)
    store.log("extracted", {"vendor": vendor["vendor_id"], "files": rec["files"], "seconds": rec["seconds"]})
    return rec
