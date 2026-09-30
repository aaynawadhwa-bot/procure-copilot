"""Turn any vendor file into (a) Claude content blocks and (b) a plain-text corpus used to verify citations.
Every representation carries addressable locations (Sheet!A12, P9, page 1, line 3) so the model can cite
and the UI can show the source."""
import base64, email, io, re
from email import policy
from pathlib import Path
from PIL import Image

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}

def kind_of(path):
    ext = Path(path).suffix.lower()
    return {".xlsx": "excel", ".xlsm": "excel", ".xls": "excel", ".csv": "csv", ".pdf": "pdf", ".docx": "word",
            ".eml": "email", ".txt": "text", ".msg": "email"}.get(ext, "image" if ext in IMG_EXT else "other")

# ---------------------------------------------------------------- per-format text
def excel_text(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"=== Sheet '{ws.title}' ===")
        merged = [str(r) for r in ws.merged_cells.ranges]
        if merged:
            out.append(f"(merged ranges: {', '.join(merged[:40])})")
        for row in ws.iter_rows():
            cells = [f"{c.coordinate}={c.value!r}" for c in row if c.value not in (None, "")]
            if cells:
                out.append(f"Row {row[0].row}: " + " | ".join(cells))
    return "\n".join(out)

def csv_text(path):
    lines = Path(path).read_text(errors="ignore").splitlines()
    return "\n".join(f"Row {i}: {l}" for i, l in enumerate(lines, 1))

def word_text(path):
    import docx
    d = docx.Document(path); out = []
    for i, p in enumerate(d.paragraphs, 1):
        if p.text.strip():
            out.append(f"[P{i}] {p.text}")
    for ti, t in enumerate(d.tables, 1):
        for ri, r in enumerate(t.rows, 1):
            out.append(f"[T{ti}R{ri}] " + " | ".join(c.text for c in r.cells))
    return "\n".join(out)

def pdf_text(path):
    import pdfplumber
    out = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            out.append(f"=== Page {i} ===\n" + (page.extract_text() or ""))
    return "\n".join(out)

def pdf_page_image(path, page_no=1, resolution=110):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        page_no = max(1, min(page_no, len(pdf.pages)))
        return pdf.pages[page_no - 1].to_image(resolution=resolution).original

def pdf_page_count(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)

def email_text(path):
    raw = Path(path).read_bytes()
    msg = email.message_from_bytes(raw, policy=policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else raw.decode("utf-8", "ignore")
    head = f"From: {msg['from']}\nTo: {msg['to']}\nDate: {msg['date']}\nSubject: {msg['subject']}\n"
    lines = text.splitlines()
    return head + "\n".join(f"[L{i}] {l}" for i, l in enumerate(lines, 1))

def email_attachments(path):
    msg = email.message_from_bytes(Path(path).read_bytes(), policy=policy.default)
    return [(p.get_filename(), p.get_content()) for p in msg.iter_attachments() if p.get_filename()]

def to_text(path):
    k = kind_of(path)
    try:
        return {"excel": excel_text, "csv": csv_text, "word": word_text, "pdf": pdf_text,
                "email": email_text, "text": lambda p: Path(p).read_text(errors="ignore")}.get(k, lambda p: "")(path)
    except Exception as e:
        return f"(could not read text: {e})"

# ---------------------------------------------------------------- Claude content blocks
def image_b64(path, max_side=1568):
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode(), im.size

def content_blocks(path):
    """Return a list of Anthropic content blocks representing this file."""
    p = Path(path); k = kind_of(p)
    header = {"type": "text", "text": f"\n##### FILE: {p.name}  (type: {k})"}
    if k == "image":
        data, size = image_b64(p)
        return [header, {"type": "text", "text": f"(image {size[0]}x{size[1]} px; give bbox as fractions of width/height)"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}]
    if k == "pdf":
        data = base64.b64encode(p.read_bytes()).decode()
        return [header, {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
                {"type": "text", "text": "Extracted text layer (use 'page N' as location):\n" + to_text(p)[:30000]}]
    return [header, {"type": "text", "text": to_text(p)[:60000]}]

# ---------------------------------------------------------------- citation verification
def _norm(s):
    return re.sub(r"[^a-z0-9.]+", " ", (s or "").lower().replace(",", "")).strip()

def verify_snippet(snippet, corpus):
    """True if the (normalised) snippet is found in the file text; tolerant to whitespace/punctuation."""
    if not snippet or not corpus:
        return False
    s, c = _norm(snippet), _norm(corpus)
    if s and s in c:
        return True
    toks = [t for t in s.split() if len(t) > 1]
    if not toks:
        return False
    hit = sum(1 for t in toks if t in c)
    return hit / len(toks) >= 0.85

def number_in_snippet(value, snippet):
    """Is the extracted number literally present in the cited snippet? Guards against invented numbers."""
    if value is None or not snippet:
        return False
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", (snippet or "").replace(",", ""))]
    return any(abs(n - float(value)) < 1e-6 for n in nums)
