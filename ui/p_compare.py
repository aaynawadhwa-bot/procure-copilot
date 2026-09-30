import pandas as pd
import streamlit as st
from ui.common import table, show_evidence, vlabel, inr, crore, CONF_BG, need_data

def page():
    st.header("4 · Side-by-side comparison")
    q = table("quotes")
    if q.empty: need_data()
    lines = table("lines"); v = table("vendors")
    st.caption("Same lines, same units (RFx UoM), same currency (INR, ex-GST). Colour = confidence. ★ = lowest landed price on the line. Blank = not quoted.")
    c = st.columns(4)
    basis = c[0].radio("Price", ["Landed (incl. freight)", "Before freight", "Annual value"], horizontal=False)
    hide_est = c[1].checkbox("Hide estimates", value=False)
    only_cleared = c[2].checkbox("Only gate-cleared vendors", value=False)
    col = {"Landed (incl. freight)": "landed_inr_per_uom", "Before freight": "price_inr_per_uom", "Annual value": "annual_value_inr"}[basis]
    qq = q.copy()
    if hide_est: qq.loc[qq.is_estimate == 1, col] = None
    if only_cleared: qq = qq[qq.vendor_id.isin(v[v.gate_status == "CLEARED"].vendor_id)]
    qq.loc[qq.review_status == "excluded", col] = None
    val = qq.pivot(index="line_id", columns="vendor_id", values=col)
    conf = qq.pivot(index="line_id", columns="vendor_id", values="confidence")
    est = qq.pivot(index="line_id", columns="vendor_id", values="is_estimate")
    mat = lines.set_index("line_id")[["description", "annual_qty", "uom", "last_year_price_inr"]].join(val)
    mat["description"] = mat["description"].str.slice(0, 60)
    vcols = list(val.columns)
    best = val.idxmin(axis=1)
    def fmt_cell(x, lid, vid):
        if pd.isna(x): return ""
        s = f"{x:,.0f}" if col == "annual_value_inr" else f"{x:,.2f}"
        if est.loc[lid, vid] == 1: s += " ~"
        if best.get(lid) == vid: s = "★ " + s
        return s
    disp = mat.copy()
    for vid in vcols:
        disp[vid] = [fmt_cell(mat.loc[lid, vid], lid, vid) for lid in mat.index]
    disp = disp.rename(columns={vid: vlabel(vid) for vid in vcols})
    def style(df):
        s = pd.DataFrame("", index=df.index, columns=df.columns)
        for vid in vcols:
            for lid in df.index:
                cf = conf.loc[lid, vid] if lid in conf.index and vid in conf.columns else None
                if isinstance(cf, str): s.loc[lid, vlabel(vid)] = f"background-color:{CONF_BG.get(cf,'')}"
                if best.get(lid) == vid: s.loc[lid, vlabel(vid)] += ";font-weight:700"
        return s
    st.dataframe(disp.style.apply(style, axis=None), use_container_width=True, height=620)
    st.caption("~ = estimate (per-kg conversion, last-year price, or estimated freight). Legend: 🟩 high/confirmed 🟨 medium 🟥 low.")
    tot = qq[qq.usable == 1].groupby("vendor_id").agg(lines=("line_id", "count"), value=("annual_value_inr", "sum")).reset_index()
    st.markdown("**Coverage & value of priced lines** (not comparable across vendors with different coverage)")
    cc = st.columns(len(tot) or 1)
    for cl, r in zip(cc, tot.itertuples()):
        g = v.set_index("vendor_id").gate_status.get(r.vendor_id)
        cl.metric(vlabel(r.vendor_id), crore(r.value), f"{r.lines}/{len(lines)} lines · {g}", delta_color="off")
    st.divider()
    st.subheader("Inspect a cell")
    a, b = st.columns(2)
    lid = a.selectbox("Line", lines.line_id.tolist(), format_func=lambda x: f"{x} · {lines.set_index('line_id').description[x][:70]}")
    vid = b.selectbox("Vendor", sorted(q.vendor_id.unique()), format_func=vlabel)
    show_evidence(vid, lid)
