"""Procure Co-pilot - RFx to award, end to end.  Run:  streamlit run app.py"""
import pandas as pd
import streamlit as st
st.set_page_config(page_title="Procure Co-pilot", page_icon="📦", layout="wide")
from core import store, llm, normalize
from core.config import MODEL, DEFAULTS
from ui import p_create, p_send, p_review, p_compare, p_quality, p_analyst, p_award
from ui.common import table

# Password lock for the hosted version. Only active when APP_PASSWORD is set (e.g. in Streamlit Cloud secrets).
import os, hmac
_APP_PASSWORD = os.getenv("APP_PASSWORD", "")
if _APP_PASSWORD and not st.session_state.get("_authed"):
    st.markdown("### 📦 Procure Co-pilot")
    _pw = st.text_input("Password", type="password")
    if _pw and hmac.compare_digest(_pw, _APP_PASSWORD):
        st.session_state["_authed"] = True
        st.rerun()
    elif _pw:
        st.error("Wrong password")
    st.stop()

store.init()
st.markdown("""<style>
.block-container{padding-top:1.4rem;max-width:1500px}
[data-testid="stMetricValue"]{font-size:1.35rem}
div[data-testid="stSidebarNav"]{display:none}
</style>""", unsafe_allow_html=True)

PAGES = {"1 · Create RFx": p_create.page, "2 · Send & receive": p_send.page, "3 · Extract & review": p_review.page,
         "4 · Compare": p_compare.page, "5 · Quality & terms": p_quality.page, "6 · Ask the analyst": p_analyst.page,
         "7 · Award & export": p_award.page, "Audit log": None}

with st.sidebar:
    st.markdown("### 📦 Procure Co-pilot")
    rfx = store.get("rfx")
    st.caption(f"RFx: **{rfx['rfx_no']}**" if rfx else "No RFx loaded")
    page = st.radio("Flow", list(PAGES), label_visibility="collapsed")
    st.divider()
    st.caption(("🟢 Claude connected · " if llm.available() else "🔴 No API key · ") + MODEL)
    with st.expander("Normalisation policy"):
        s = normalize.settings()
        fe = st.number_input("Freight estimate when vendor says 'extra' (₹/kg)", 0.0, 10.0, float(s["freight_estimate_inr_per_kg"]), 0.1)
        ot = st.slider("Outlier flag threshold (% vs median)", 10, 80, int(s["outlier_threshold_pct"]))
        gm = st.radio("Gate mode", ["strict", "lenient"], index=0 if s["gate_mode"] == "strict" else 1)
        ie = st.checkbox("Include estimates in award by default", s["include_estimates_in_award"])
        if st.button("Apply policy"):
            store.put("settings", {"freight_estimate_inr_per_kg": fe, "outlier_threshold_pct": ot, "gate_mode": gm, "include_estimates_in_award": ie})
            store.log("policy_changed", {"freight": fe, "outlier": ot, "gate": gm, "estimates": ie}, actor="buyer")
            normalize.rebuild(); st.rerun()
    with st.expander("Reset"):
        if st.button("Reset everything (keeps LLM cache)"):
            store.reset_all(); [st.session_state.pop(k) for k in list(st.session_state.keys())]; st.rerun()

if PAGES[page]:
    PAGES[page]()
else:
    st.header("Audit log")
    ev = pd.DataFrame(store.events(500), columns=["ts", "actor", "event", "detail"])
    if not ev.empty:
        ev["ts"] = pd.to_datetime(ev.ts, unit="s").dt.strftime("%Y-%m-%d %H:%M:%S")
    st.dataframe(ev, hide_index=True, use_container_width=True, height=700)
