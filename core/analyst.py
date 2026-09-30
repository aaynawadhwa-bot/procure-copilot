"""The analyst: a tool-using Claude agent over the normalised comparison.
It never sees raw vendor files and never does arithmetic in its head - it queries SQLite, runs award
scenarios through the same deterministic engine as the UI, and must disclose exclusions and assumptions."""
import json, re, time
import pandas as pd
from . import llm, store, award
from .config import EXPORT_DIR

DATA_DICTIONARY = """
TABLES (SQLite; all money in INR, ex-GST, per RFx unit of measure unless stated):
lines(line_id, material_code, description, type, ply, uom, annual_qty, est_weight_kg, last_year_price_inr, last_year_vendor)
vendors(vendor_id, vendor_name, city, gate_status [CLEARED|CONDITIONAL|INCOMPLETE|UNKNOWN|NOT CLEARED], gate_reasons,
        currency, price_basis, freight_type, payment_days, validity_days, lines_priced, lines_total, needs_review, annual_value_priced_lines)
quotes(vendor_id, line_id, status [quoted|not_quoted|declined|unclear|unresolvable], quote_form, raw_price, raw_unit, raw_currency,
       price_inr_per_uom (before freight), freight_inr_per_uom, landed_inr_per_uom (= price + freight; USE THIS TO COMPARE),
       annual_qty, uom, description, annual_value_inr, is_estimate (1 = rests on an assumption: estimated freight or 'same as last year'),
       is_derived (1 = converted from a per-kg rate using the RFx spec weight; vendor bills actual weight),
       confidence [high|medium|low|confirmed], confidence_score, review_status [auto|needs_review|confirmed|overridden|excluded],
       usable (1 = has a landed price and not excluded), derivation (how the number was computed), flags (text),
       source_file, source_location, source_snippet, vendor_item_text, match_confidence, ambiguity, override_note)
flags(vendor_id, line_id (NULL = vendor-level), severity [critical|review|info], code, message, resolved)
questionnaire(vendor_id, q_id, question, knock_out, criterion, answer_text, result [pass|fail|conditional|unknown|info], reason, source_location)
terms(vendor_id, term, value, rfx_requirement, deviation [better|worse|as requested|unknown|conditional|note])
alternates(vendor_id, line_id, description, deviation, raw_price, price_inr_per_uom, landed_inr_per_uom)   -- non-compliant alternative offers
documents(vendor_id, file, doc_type, holder_name, standard, certificate_no, issue_date, expiry_date, valid_on_due_date, key_findings)
unmatched_items(vendor_id, text, price, reason)
"""

SYSTEM = """You are the procurement analyst for a category buyer. You answer questions about an RFx comparison
that has already been extracted and normalised. The buyer may award crores of rupees on your answer.

How you work:
- ALWAYS get numbers from tools (run_sql / award_scenario). Never compute totals, savings or averages in your head:
  write SQL for it. Never invent numbers.
- Compare vendors on landed_inr_per_uom (includes freight). Use usable=1 rows.
- For award / split / 'cheapest' questions use award_scenario - it applies the quality gate, excludes estimates
  by default and reports uncovered lines. Quote its notes.
- "Cleared the quality questionnaire" = vendors.gate_status = 'CLEARED'. Mention CONDITIONAL vendors separately
  and what would change if they cleared.
- Every answer must state: what was excluded and why, which numbers are estimates or still need review, and any
  line not covered. Be brief about it, but never skip it.
- When a table or chart helps, call show_table / make_chart (the buyer sees them). Offer export_excel for anything
  the buyer may forward.
- Refer to vendors by name and id (e.g. 'Shivam (V1)'), lines by id. Money: Rs with Indian grouping or lakh/crore.
- If the data cannot answer the question, say exactly what is missing and suggest the clarification to send.
- Be concise: lead with the answer, then the evidence, then caveats.
""" + DATA_DICTIONARY

TOOLS = [
 {"name": "run_sql", "description": "Run a read-only SQLite SELECT over the comparison tables. Returns up to 200 rows as JSON.",
  "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}, "purpose": {"type": "string"}}, "required": ["sql"]}},
 {"name": "award_scenario", "description": "Deterministic award engine. Returns total, per-line winner, runner-up, uncovered lines and caveats.",
  "input_schema": {"type": "object", "properties": {
      "vendors": {"type": "array", "items": {"type": "string"}, "description": "vendor_ids allowed; omit to use vendors that cleared the gate"},
      "strategy": {"type": "string", "enum": ["line_wise_cheapest", "single_vendor"]},
      "single_vendor": {"type": "string"}, "include_estimates": {"type": "boolean"},
      "include_unconfirmed": {"type": "boolean", "description": "include prices still needing review (default true)"},
      "exclude_lines": {"type": "array", "items": {"type": "string"}},
      "gate_mode": {"type": "string", "enum": ["strict", "lenient"], "description": "lenient also admits CONDITIONAL vendors"}}}},
 {"name": "show_table", "description": "Display a table to the buyer from a SELECT query.",
  "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}, "title": {"type": "string"}}, "required": ["sql", "title"]}},
 {"name": "make_chart", "description": "Display a chart to the buyer from a SELECT query.",
  "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}, "chart_type": {"type": "string", "enum": ["bar", "grouped_bar", "stacked_bar", "line", "scatter", "pie", "heatmap"]},
      "x": {"type": "string"}, "y": {"type": "string"}, "color": {"type": "string"}, "title": {"type": "string"}}, "required": ["sql", "chart_type", "x", "y", "title"]}},
 {"name": "export_excel", "description": "Create an Excel file (one sheet per query) the buyer can download.",
  "input_schema": {"type": "object", "properties": {"filename": {"type": "string"},
      "sheets": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "sql": {"type": "string"}}, "required": ["name", "sql"]}}},
      "required": ["filename", "sheets"]}},
 {"name": "get_evidence", "description": "Source evidence for one vendor/line: raw quote, snippet, file location, derivation, flags.",
  "input_schema": {"type": "object", "properties": {"vendor_id": {"type": "string"}, "line_id": {"type": "string"}}, "required": ["vendor_id", "line_id"]}},
]

FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace|vacuum)\b", re.I)

def _sql(sql, limit=200):
    if FORBIDDEN.search(sql) or not sql.strip().lower().startswith(("select", "with")):
        raise ValueError("Only read-only SELECT queries are allowed.")
    df = store.read_sql(sql)
    return df, len(df) > limit

def _df_json(df, limit=200):
    return json.loads(df.head(limit).to_json(orient="records", double_precision=4))

def run_tool(name, args, artifacts):
    if name == "run_sql":
        df, trunc = _sql(args["sql"])
        return {"rows": _df_json(df), "row_count": len(df), "truncated": trunc}
    if name == "award_scenario":
        sc = award.scenario(vendors=args.get("vendors"), strategy=args.get("strategy", "line_wise_cheapest"),
                            single_vendor=args.get("single_vendor"), include_estimates=args.get("include_estimates"),
                            include_unconfirmed=args.get("include_unconfirmed", True), exclude_lines=args.get("exclude_lines"),
                            gate_mode=args.get("gate_mode"))
        artifacts.append({"kind": "table", "title": f"Award scenario - {', '.join(sc['vendors_considered'])} ({sc['strategy']})",
                          "df": sc["award"][["line_id", "vendor_id", "landed_inr_per_uom", "annual_value_inr", "runner_up", "runner_up_landed", "confidence", "review_status"]]})
        return award.scenario_summary(sc)
    if name == "show_table":
        df, _ = _sql(args["sql"]); artifacts.append({"kind": "table", "title": args["title"], "df": df})
        return {"shown_rows": len(df), "columns": list(df.columns), "first_rows": _df_json(df, 15)}
    if name == "make_chart":
        df, _ = _sql(args["sql"]); artifacts.append({"kind": "chart", "spec": args, "df": df})
        return {"charted_rows": len(df), "columns": list(df.columns)}
    if name == "export_excel":
        fn = re.sub(r"[^A-Za-z0-9_.-]", "_", args["filename"]);
        fn = fn if fn.endswith(".xlsx") else fn + ".xlsx"
        path = EXPORT_DIR / fn
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            for sh in args["sheets"]:
                df, _ = _sql(sh["sql"], limit=10**6); df.to_excel(xw, sheet_name=sh["name"][:31], index=False)
        artifacts.append({"kind": "file", "path": str(path)})
        store.log("export", {"file": fn}, actor="analyst")
        return {"file": fn, "sheets": [s["name"] for s in args["sheets"]]}
    if name == "get_evidence":
        q = store.read_sql("SELECT * FROM quotes WHERE vendor_id=? AND line_id=?", (args["vendor_id"], args["line_id"]))
        f = store.read_sql("SELECT severity, code, message, resolved FROM flags WHERE vendor_id=? AND (line_id=? OR line_id IS NULL)", (args["vendor_id"], args["line_id"]))
        artifacts.append({"kind": "evidence", "vendor_id": args["vendor_id"], "line_id": args["line_id"]})
        return {"quote": _df_json(q), "flags": _df_json(f)}
    raise ValueError(f"unknown tool {name}")

def ask(history, question, max_steps=12):
    """history: list of prior {'role','content'} (text only). Returns dict(answer, artifacts, trace)."""
    msgs = [m for m in history if m["role"] in ("user", "assistant")] + [{"role": "user", "content": question}]
    artifacts, trace = [], []
    for step in range(max_steps):
        blocks, stop = llm.chat(SYSTEM, msgs, tools=TOOLS, max_tokens=4000)
        msgs.append({"role": "assistant", "content": blocks})
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not uses:
            answer = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            store.log("analyst_q", {"q": question, "steps": step + 1}, actor="buyer")
            return {"answer": answer, "artifacts": artifacts, "trace": trace}
        results = []
        for u in uses:
            t0 = time.time()
            try:
                out = run_tool(u["name"], u.get("input", {}), artifacts); ok = True
            except Exception as e:
                out = {"error": str(e)}; ok = False
            trace.append({"tool": u["name"], "input": u.get("input"), "ok": ok, "ms": int((time.time() - t0) * 1000),
                          "output_preview": json.dumps(out, default=str)[:1500]})
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(out, default=str)[:60000], "is_error": not ok})
        msgs.append({"role": "user", "content": results})
    return {"answer": "I hit my step limit before finishing - try a narrower question.", "artifacts": artifacts, "trace": trace}
