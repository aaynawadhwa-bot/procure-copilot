"""Vendor communication. The transport is STUBBED (no SMTP): 'sending' writes to an outbox log and
'receiving' drops files into data/inbox/<vendor>/. The clarification email drafting is a real AI loop."""
import shutil, time
from pathlib import Path
from . import store, llm
from .config import INBOX_DIR, DEMO_RESPONSES, DEMO_DIR

DEMO_FOLDERS = {"V1": "V1_Shivam_Corrupack", "V2": "V2_Pacific_Fibreboard", "V3": "V3_Deccan_Corrugated", "V4": "V4_Rathi_Kraft", "V5": "V5_OmSai_Corrugators"}

def rfx_email(rfx, vendor):
    return {"to": vendor.get("email") or "(no email on file)", "subject": f"RFQ {rfx['rfx_no']} - {rfx['title']}",
            "body": f"Dear {vendor.get('contact') or vendor['name']},\n\n{rfx.get('buyer', {}).get('company', 'We')} invites your quotation for "
                    f"{rfx['title']} ({len(rfx['line_items'])} line items). Please find the RFQ and response template attached.\n\n"
                    f"Bid due: {rfx['due']}. Price basis: {rfx['price_basis']}. Payment: {rfx['payment_terms']}.\n"
                    f"Reply in any format you prefer - we read it all.\n\nRegards,\n{rfx.get('buyer', {}).get('buyer_name', 'Buyer')}",
            "attachments": ["RFQ.pdf", "Response_Template.xlsx"]}

def send_rfx(rfx):
    out = []
    for v in rfx["invited_vendors"]:
        e = rfx_email(rfx, v); e.update(vendor_id=v["vendor_id"], sent_at=time.strftime("%Y-%m-%d %H:%M"), transport="stub (not actually emailed)")
        out.append(e)
    store.put("outbox", out); store.log("rfx_sent", {"vendors": [o["vendor_id"] for o in out]}, actor="buyer")
    return out

def receive_demo_replies():
    got = {}
    for vid, folder in DEMO_FOLDERS.items():
        src = Path(DEMO_RESPONSES) / folder; dst = INBOX_DIR / vid; dst.mkdir(parents=True, exist_ok=True)
        for f in src.glob("*"):
            if f.is_file():
                shutil.copy2(f, dst / f.name); got.setdefault(vid, []).append(f.name)
    store.put("inbox_received_at", time.strftime("%Y-%m-%d %H:%M")); store.log("replies_received", got)
    return got

def save_upload(vendor_id, name, data: bytes):
    d = INBOX_DIR / vendor_id; d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data); store.log("file_uploaded", {"vendor": vendor_id, "file": name}, actor="buyer")

def inbox():
    return {d.name: sorted(p.name for p in d.glob("*") if p.is_file()) for d in sorted(INBOX_DIR.glob("*")) if d.is_dir()}

CLARIFY_SYSTEM = """You draft short, polite, specific clarification emails from an Indian category buyer to a supplier.
Only ask about the open issues listed (group them; reference RFx line ids and the supplier's own wording). Ask for
answers in a form that removes ambiguity (e.g. 'price per piece delivered Bhiwandi, ex-GST'). Give a reply-by date
two working days out. No fluff, under 200 words. Output: 'Subject: ...' line, blank line, body."""

def draft_clarification(rfx, vendor, issues):
    lines = "\n".join(f"- [{i['severity']}] {i.get('line_id') or 'general'}: {i['message']}" for i in issues)
    prompt = (f"RFx {rfx['rfx_no']} ({rfx['title']}), due {rfx['due']}. Buyer: {rfx.get('buyer', {}).get('buyer_name')}.\n"
              f"Supplier: {vendor['name']} (contact {vendor.get('contact', '')}).\nOpen issues found in their response:\n{lines}")
    return llm.text(CLARIFY_SYSTEM, prompt, max_tokens=900)
