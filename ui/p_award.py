import streamlit as st
import plotly.express as px
from core import award, exporter, store, llm
from core.normalize import settings
from ui.common import table, vlabel, inr, crore, need_data

def page():
    st.header("7 · Award decision & export")
    v = table("vendors")
    if v.empty: need_data()
    rfx = store.get("rfx"); s = settings()
    c = st.columns([2, 1.2, 1, 1])
    mode = c[1].radio("Gate mode", ["strict", "lenient"], index=0 if s["gate_mode"] == "strict" else 1, help="lenient also admits CONDITIONAL vendors")
    default = award.eligible_vendors(mode)
    vendors = c[0].multiselect("Vendors allowed", v.vendor_id.tolist(), default=default, format_func=lambda x: f"{vlabel(x)} · {v.set_index('vendor_id').gate_status[x]}")
    strategy = c[2].radio("Strategy", ["line_wise_cheapest", "single_vendor"])
    inc_est = c[3].checkbox("Include estimates", value=s["include_estimates_in_award"])
    inc_unc = c[3].checkbox("Include unconfirmed", value=True)
    single = st.selectbox("Single vendor", vendors, format_func=vlabel) if strategy == "single_vendor" and vendors else None
    if not vendors:
        st.warning("No vendors selected / none cleared the gate."); return
    sc = award.scenario(vendors=vendors, strategy=strategy, single_vendor=single, include_estimates=inc_est, include_unconfirmed=inc_unc, gate_mode=mode)
    m = st.columns(4)
    m[0].metric("Award value (annual)", crore(sc["total_inr"]))
    m[1].metric("Lines covered", f"{sc['lines_covered']}/{sc['lines_total']}")
    ly = sc["like_for_like_vs_last_year"]
    if ly["last_year_inr"]:
        m[2].metric("vs last year (like-for-like)", crore(ly["this_award_inr"] - ly["last_year_inr"]), f"{(ly['this_award_inr']/ly['last_year_inr']-1)*100:+.1f}% on {ly['lines']} lines", delta_color="inverse")
    others = [x for x in v.vendor_id if x in vendors]
    if strategy == "line_wise_cheapest" and len(vendors) > 1:
        singles = {x: award.scenario(vendors=[x], include_estimates=inc_est, include_unconfirmed=inc_unc) for x in vendors}
        full = {x: s_ for x, s_ in singles.items() if s_["lines_covered"] == sc["lines_covered"]}
        if full:
            bx = min(full, key=lambda k: full[k]["total_inr"])
            m[3].metric(f"Saving vs best single vendor ({vlabel(bx)})", crore(full[bx]["total_inr"] - sc["total_inr"]))
    for n in sc["notes"]:
        st.warning(n)
    left, right = st.columns([1.6, 1])
    with left:
        a = sc["award"].copy()
        st.dataframe(a[["line_id", "description", "annual_qty", "vendor_id", "landed_inr_per_uom", "annual_value_inr", "runner_up", "runner_up_landed", "confidence", "review_status", "is_estimate"]],
                     hide_index=True, use_container_width=True, height=520)
    with right:
        bv = sc["by_vendor"].copy(); bv["vendor"] = bv.vendor_id.map(vlabel)
        if len(bv):
            st.plotly_chart(px.pie(bv, names="vendor", values="value_inr", title="Award split by value", hole=0.45).update_layout(height=320, margin=dict(t=40, b=0)), use_container_width=True)
            st.dataframe(bv.assign(value=bv.value_inr.map(crore))[["vendor", "lines", "value"]], hide_index=True, use_container_width=True)
    st.divider()
    e1, e2 = st.columns(2)
    with e1:
        if st.button("📊 Export full comparison + award to Excel", type="primary"):
            p = exporter.export_workbook(sc); st.session_state["xl"] = str(p)
        if st.session_state.get("xl"):
            from pathlib import Path
            p = Path(st.session_state["xl"])
            st.download_button(f"⬇️ {p.name}", p.read_bytes(), file_name=p.name, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with e2:
        note = st.text_input("Context for the memo (optional)", placeholder="e.g. VP wants dual sourcing on 5-ply")
        if st.button("📝 Draft award recommendation memo", disabled=not llm.available()):
            with st.spinner("Drafting from the scenario numbers…"):
                st.session_state["memo"] = exporter.award_memo(rfx, sc, note)
    if st.session_state.get("memo"):
        st.markdown(st.session_state["memo"])
        st.download_button("⬇️ Memo (.md)", st.session_state["memo"], file_name="award_memo.md")
