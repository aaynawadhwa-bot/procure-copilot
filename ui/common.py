import re
from pathlib import Path
import pandas as pd
import streamlit as st
from core import store, readers
from core.config import INBOX_DIR

SEV_ICON = {"critical": "🔴", "review": "🟡", "info": "🔵"}
CONF_BG = {"high": "#e2f0d9", "confirmed": "#c6e0b4", "medium": "#fff2cc", "low": "#f8cbad"}
GATE_COLOR = {"CLEARED": "#2e7d32", "CONDITIONAL": "#b26a00", "INCOMPLETE": "#b26a00", "UNKNOWN": "#616161", "NOT CLEARED": "#c62828"}

def inr(x, dec=0):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    neg = x < 0; x = abs(float(x))
    s = f"{x:.{dec}f}"; whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join(re.findall(r"\d{1,2}(?=(?:\d{2})*$)", head)) if head else ""
        whole = head + "," + tail
    return ("-" if neg else "") + "₹" + whole + (("." + frac) if frac else "")

def crore(x):
    if x is None or pd.isna(x): return "—"
    return f"₹{x/1e7:.2f} Cr" if abs(x) >= 1e7 else f"₹{x/1e5:.1f} L"

def table(name):
    try:
        return store.read_sql(f"SELECT * FROM {name}")
    except Exception:
        return pd.DataFrame()

def ready():
    return not table("quotes").empty

def vendor_names():
    rfx = store.get("rfx") or {}
    return {v["vendor_id"]: v["name"] for v in rfx.get("invited_vendors", [])}

def vlabel(vid):
    n = vendor_names().get(vid, vid)
    return f"{n.split()[0]} ({vid})"

def gate_badge(status):
    c = GATE_COLOR.get(status, "#616161")
    return f"<span style='background:{c};color:white;padding:2px 8px;border-radius:10px;font-size:0.8em;font-weight:600'>{status}</span>"

def need_data(msg="Run extraction first (step 3)."):
    st.info(msg); st.stop()

def _loc_page(loc):
    m = re.search(r"page\s*(\d+)", loc or "", re.I); return int(m.group(1)) if m else 1

def show_evidence(vid, lid, key=""):
    """Source panel: exactly where a number came from, next to how it was derived."""
    q = store.read_sql("SELECT * FROM quotes WHERE vendor_id=? AND line_id=?", (vid, lid))
    if q.empty:
        st.warning("No record."); return
    r = q.iloc[0]
    rec = store.get(f"extraction:{vid}") or {}
    c1, c2 = st.columns([1.15, 1])
    with c1:
        st.markdown(f"**Source** · `{r.source_file or '—'}` · {r.source_location or ''}")
        if r.source_snippet:
            st.markdown(f"<div style='background:#fffbe6;border-left:4px solid #f0b400;padding:8px 10px;font-family:monospace;font-size:0.85em;white-space:pre-wrap'>{r.source_snippet}</div>", unsafe_allow_html=True)
        f = INBOX_DIR / vid / (r.source_file or "")
        if r.source_file and f.exists():
            kind = readers.kind_of(f)
            try:
                if kind == "image":
                    from PIL import Image
                    im = Image.open(f)
                    if r.bbox:
                        b = [float(x) for x in str(r.bbox).split(",")]
                        if len(b) == 4:
                            W, H = im.size; pad = 0.03
                            box = (max(0, (b[0]-pad)*W), max(0, (b[1]-pad)*H), min(W, (b[2]+pad)*W), min(H, (b[3]+pad)*H))
                            if box[2] > box[0] and box[3] > box[1]:
                                st.image(im.crop(box), caption="Approximate region located by the model (verify)")
                    with st.expander("Full photo"):
                        st.image(im)
                elif kind == "pdf":
                    st.image(readers.pdf_page_image(f, _loc_page(r.source_location)), caption=f"{r.source_file} · page {_loc_page(r.source_location)}")
                else:
                    corpus = (rec.get("corpus") or {}).get(r.source_file, "")
                    snippet_tokens = (r.source_snippet or "")[:40]
                    lines_ = corpus.splitlines()
                    hit = next((i for i, l in enumerate(lines_) if snippet_tokens and snippet_tokens[:25].lower() in l.lower()), None)
                    ctx = lines_[max(0, hit-2): hit+3] if hit is not None else lines_[:8]
                    with st.expander("File context", expanded=True):
                        st.code("\n".join(ctx)[:3000], language=None)
            except Exception as e:
                st.caption(f"(preview unavailable: {e})")
    with c2:
        st.markdown(f"**{vlabel(vid)} · {lid}**  \n{r.description}")
        st.markdown(f"Vendor item: _{r.vendor_item_text or '—'}_  \nMatch confidence: **{(r.match_confidence or 0):.0%}**")
        st.markdown(f"Quoted: **{r.raw_price if r.raw_price is not None else '—'}** {r.raw_currency or ''} {r.raw_unit or ''}  \n"
                    f"Normalised: **{inr(r.price_inr_per_uom, 2)}** + freight {inr(r.freight_inr_per_uom, 2)} = **{inr(r.landed_inr_per_uom, 2)} / {r.uom}**")
        st.markdown(f"Status: `{r.status}` · Confidence: `{r.confidence}` ({r.confidence_score:.2f}) · Review: `{r.review_status}`" + (" · **estimate**" if r.is_estimate else ""))
        if r.derivation:
            st.caption("How it was computed: " + r.derivation)
        if r.ambiguity:
            st.warning("Ambiguity: " + r.ambiguity)
        fl = store.read_sql("SELECT severity, message, resolved FROM flags WHERE vendor_id=? AND line_id=?", (vid, lid))
        for x in fl.itertuples():
            st.markdown(f"{SEV_ICON.get(x.severity,'')} {'~~' if x.resolved else ''}{x.message}{'~~ (resolved)' if x.resolved else ''}")
        if r.override_note:
            st.success(r.override_note)

def render_chart(spec, df):
    import plotly.express as px
    t = spec.get("chart_type", "bar"); x, y, c = spec.get("x"), spec.get("y"), spec.get("color") or None
    try:
        if t in ("bar", "grouped_bar", "stacked_bar"):
            fig = px.bar(df, x=x, y=y, color=c, title=spec.get("title"), barmode="stack" if t == "stacked_bar" else "group")
        elif t == "line": fig = px.line(df, x=x, y=y, color=c, title=spec.get("title"), markers=True)
        elif t == "scatter": fig = px.scatter(df, x=x, y=y, color=c, title=spec.get("title"))
        elif t == "pie": fig = px.pie(df, names=x, values=y, title=spec.get("title"))
        elif t == "heatmap":
            p = df.pivot_table(index=y, columns=x, values=c, aggfunc="first"); fig = px.imshow(p, text_auto=".1f", aspect="auto", title=spec.get("title"))
        else: fig = px.bar(df, x=x, y=y, color=c, title=spec.get("title"))
        fig.update_layout(height=420, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.caption(f"Chart could not be drawn ({e}); showing data."); st.dataframe(df, hide_index=True)
