import time
import pandas as pd
import streamlit as st
from core import store, normalize
from ui.common import table, vlabel, gate_badge, need_data

RES_BG = {"pass": "#e2f0d9", "fail": "#f8cbad", "conditional": "#fff2cc", "unknown": "#eeeeee", "info": "white"}

def page():
    st.header("5 · Quality gate, documents & terms")
    v = table("vendors")
    if v.empty: need_data()
    qa = table("questionnaire"); docs = table("documents"); terms = table("terms")
    st.caption("Gate rule: every knock-out question must pass; the ISO answer must be backed by a certificate valid on the bid due date. Claims are checked against the attached documents.")
    cols = st.columns(len(v))
    for c, r in zip(cols, v.itertuples()):
        c.markdown(f"**{vlabel(r.vendor_id)}**<br>{gate_badge(r.gate_status)}", unsafe_allow_html=True)
        c.caption(r.gate_reasons[:300])
    st.subheader("Questionnaire")
    piv = qa.pivot(index="q_id", columns="vendor_id", values="result")
    txt = qa.assign(cell=lambda d: d.result.str.upper() + " · " + d.answer_text.fillna("—").str.slice(0, 60)).pivot(index="q_id", columns="vendor_id", values="cell")
    qmeta = qa.drop_duplicates("q_id").set_index("q_id")[["question", "knock_out"]]
    disp = qmeta.join(txt); disp["knock_out"] = disp.knock_out.map({1: "KO", 0: ""})
    def sty(df):
        s = pd.DataFrame("", index=df.index, columns=df.columns)
        for vid in piv.columns:
            for qid in df.index:
                s.loc[qid, vid] = f"background-color:{RES_BG.get(piv.loc[qid, vid], 'white')}"
        return s
    st.dataframe(disp.rename(columns=vlabel).style.apply(lambda d: sty(disp).rename(columns=vlabel), axis=None), use_container_width=True, height=470)
    with st.expander("Why each knock-out result"):
        st.dataframe(qa[qa.knock_out == 1][["vendor_id", "q_id", "result", "reason", "source_location"]], hide_index=True, use_container_width=True)
    st.subheader("Attached documents (what they actually say)")
    if docs.empty: st.caption("No documents attached by any vendor.")
    else:
        d = docs.copy(); d["valid_on_due_date"] = d.valid_on_due_date.map({1: "✅ valid", 0: "❌ EXPIRED"}).fillna("—")
        st.dataframe(d, hide_index=True, use_container_width=True)
    st.subheader("Commercial terms vs RFx")
    tp = terms.groupby(["term", "vendor_id"]).agg(value=("value", lambda x: " / ".join(str(i) for i in x if i)), dev=("deviation", "first")).reset_index()
    tv = tp.pivot(index="term", columns="vendor_id", values="value"); td = tp.pivot(index="term", columns="vendor_id", values="dev")
    DEVBG = {"worse": "#f8cbad", "better": "#e2f0d9", "conditional": "#fff2cc", "unknown": "#eeeeee"}
    st.dataframe(tv.rename(columns=vlabel).style.apply(lambda d: pd.DataFrame([[f"background-color:{DEVBG.get(td.loc[i, c], '')}" for c in td.columns] for i in d.index], index=d.index, columns=d.columns), axis=None), use_container_width=True)
    st.subheader("Buyer override of gate status")
    st.caption("E.g. the vendor sends the missing certificate by email. Overrides are logged with your reason and shown everywhere.")
    a, b, c, d = st.columns([1, 1, 2, 1])
    vid = a.selectbox("Vendor", v.vendor_id.tolist(), format_func=vlabel)
    stt = b.selectbox("Set status", ["CLEARED", "CONDITIONAL", "NOT CLEARED", "(remove override)"])
    why = c.text_input("Reason", placeholder="ISO certificate TQC/IN/QMS/14-20931 received by email 24-Sep, valid to Nov-2027")
    if d.button("Apply"):
        g = store.get("gate_overrides", {}) or {}
        if stt == "(remove override)": g.pop(vid, None)
        else: g[vid] = {"status": stt, "reason": why, "ts": time.strftime("%Y-%m-%d %H:%M")}
        store.put("gate_overrides", g); store.log("gate_override", {"vendor": vid, "status": stt, "reason": why}, actor="buyer")
        normalize.rebuild(); st.rerun()
