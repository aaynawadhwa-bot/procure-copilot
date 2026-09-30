"""RFx co-pilot: a conversational agent that drafts the RFx (scope, lines, questionnaire, terms) as structured
data via a tool call, so the draft is always a valid, editable object - not prose."""
import json
from . import llm
from .rfx import complete_line

LINE = {"type": "object", "properties": {
    "line_id": {"type": "string"}, "material_code": {"type": "string"}, "type": {"type": "string", "description": "RSC|HSC|MAILER|TRAY|PARTITION|PAD|ROLL|INSERT|BIN|OTHER"},
    "ply": {"type": "integer"}, "L_mm": {"type": "number"}, "W_mm": {"type": "number", "description": "for ROLL: length in mm"}, "H_mm": {"type": "number"},
    "paper_gsm": {"type": "array", "items": {"type": "number"}, "description": "liner/flute/... GSMs, outer to inner"}, "bf": {"type": "number"},
    "print": {"type": "string"}, "annual_qty": {"type": "number"}, "uom": {"type": "string"}, "description": {"type": "string"}, "note": {"type": "string"},
    "cells": {"type": ["array", "null"], "items": {"type": "integer"}}}, "required": ["line_id", "type", "description", "annual_qty", "uom"]}
DRAFT = {"type": "object", "properties": {
    "rfx_no": {"type": "string"}, "title": {"type": "string"}, "category": {"type": "string"}, "issued": {"type": "string"}, "due": {"type": "string"},
    "contract_period": {"type": "string"}, "price_basis": {"type": "string"}, "payment_terms": {"type": "string"},
    "price_validity_required_days": {"type": "integer"}, "delivery_location": {"type": "string"},
    "line_items": {"type": "array", "items": LINE},
    "questionnaire": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "question": {"type": "string"}, "answer_type": {"type": "string"}, "knock_out": {"type": "boolean"},
        "criterion": {"type": "string", "description": "machine-checkable where possible: '>= 300 MT/month', '<= 10 days', 'Must be YES', 'certificate valid on bid due date'"}},
        "required": ["id", "question", "knock_out", "criterion"]}},
    "terms": {"type": "array", "items": {"type": "string"}},
    "invited_vendors": {"type": "array", "items": {"type": "object", "properties": {
        "vendor_id": {"type": "string"}, "name": {"type": "string"}, "email": {"type": "string"}, "city": {"type": "string"}}, "required": ["vendor_id", "name"]}},
    "open_questions": {"type": "array", "items": {"type": "string"}, "description": "things you still need from the buyer"}},
    "required": ["title", "line_items", "questionnaire"]}

SYSTEM = """You are an RFx co-pilot for a category buyer in an Indian manufacturing/FMCG company. Through a short
conversation you turn a rough need into a complete, sourcing-ready RFx: scope, line items with full specs, a
supplier questionnaire with clear knock-out criteria, and commercial terms.

Behave like a sharp senior buyer:
- Ask at most 2-3 focused questions at a time; propose sensible defaults instead of asking everything.
- For corrugated packaging, every line needs: type, ply, internal L x W x H mm, paper GSM per layer, BF, print, annual qty, UoM.
- Make knock-out criteria measurable (numbers, 'Must be YES', 'certificate valid on bid due date'). Default
  terms: delivered to buyer's warehouse, ex-GST, INR, 45-day payment, 180-day validity, line-wise award allowed.
- Whenever the draft changes, call update_rfx_draft with the COMPLETE current draft (not a diff). Then reply briefly
  with what changed and what you still need (open_questions).
- Never invent a supplier's email; leave blank if unknown."""

TOOLS = [{"name": "update_rfx_draft", "description": "Replace the current RFx draft with this complete version.", "input_schema": DRAFT}]

def turn(history, user_msg, draft):
    """history: list of {'role','content'} text messages. Returns (reply_text, new_draft)."""
    ctx = f"CURRENT DRAFT (JSON):\n{json.dumps(draft, ensure_ascii=False)[:30000]}" if draft else "No draft yet."
    msgs = [m for m in history if m["role"] in ("user", "assistant")] + [{"role": "user", "content": f"{ctx}\n\nBUYER: {user_msg}"}]
    new_draft = draft
    for _ in range(4):
        blocks, stop = llm.chat(SYSTEM, msgs, tools=TOOLS, max_tokens=12000, temperature=0.3)
        msgs.append({"role": "assistant", "content": blocks})
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not uses:
            return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip(), new_draft
        res = []
        for u in uses:
            d = u.get("input", {})
            for l in d.get("line_items", []):
                try: complete_line(l)
                except Exception: pass
            new_draft = d
            res.append({"type": "tool_result", "tool_use_id": u["id"], "content": f"Draft saved: {len(d.get('line_items', []))} lines, {len(d.get('questionnaire', []))} questions."})
        msgs.append({"role": "user", "content": res})
    return "Draft updated.", new_draft
