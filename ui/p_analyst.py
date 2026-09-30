import json
from pathlib import Path
import streamlit as st
from core import analyst, llm, store
from ui.common import render_chart, show_evidence, need_data, table

SUGGESTED = [
    "Who is cheapest overall on landed cost, and what are you assuming?",
    "What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire?",
    "Why did Pacific fail the quality gate? Show me the evidence.",
    "If Deccan sends its ISO certificate, how much does a Shivam + Deccan split save versus awarding everything to Shivam?",
    "Which numbers should I double-check before I sign? Rank by rupee impact.",
    "Chart the landed price per line for the 5-ply boxes across all vendors.",
    "Does Pacific's rebate change the ranking? At what award size does it kick in?",
    "Export the recommended award with sources to Excel.",
]

def render_artifacts(arts, key):
    for i, a in enumerate(arts):
        if a["kind"] == "table":
            st.markdown(f"**{a['title']}**"); st.dataframe(a["df"], hide_index=True, use_container_width=True)
        elif a["kind"] == "chart":
            render_chart(a["spec"], a["df"])
        elif a["kind"] == "file":
            p = Path(a["path"])
            if p.exists():
                st.download_button(f"⬇️ Download {p.name}", p.read_bytes(), file_name=p.name, key=f"dl_{key}_{i}",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        elif a["kind"] == "evidence":
            with st.expander(f"Evidence · {a['vendor_id']} · {a['line_id']}"):
                show_evidence(a["vendor_id"], a["line_id"], key=f"{key}_{i}")

def page():
    st.header("6 · Ask the analyst")
    if table("quotes").empty: need_data()
    if not llm.available():
        st.warning("Set ANTHROPIC_API_KEY to use the analyst."); return
    st.caption("Natural language over the normalised comparison. Every number comes from a SQL query or the award engine - open 'How I got this' to audit it.")
    ss = st.session_state; ss.setdefault("an_msgs", [])
    for i, m in enumerate(ss.an_msgs):
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m["role"] == "assistant":
                render_artifacts(m.get("artifacts", []), key=i)
                if m.get("trace"):
                    with st.expander(f"How I got this ({len(m['trace'])} tool calls)"):
                        for t in m["trace"]:
                            st.markdown(f"**{t['tool']}** {'✅' if t['ok'] else '❌'} · {t['ms']} ms")
                            st.code(json.dumps(t["input"], indent=1)[:2500], language="json")
    if not ss.an_msgs:
        st.markdown("**Try:**")
        cols = st.columns(2)
        for i, s in enumerate(SUGGESTED):
            if cols[i % 2].button(s, key=f"sg{i}", use_container_width=True):
                ss._an_pending = s; st.rerun()
    q = st.chat_input("Ask anything about the bids…") or ss.pop("_an_pending", None)
    if q:
        hist = [{"role": m["role"], "content": m["content"]} for m in ss.an_msgs][-10:]
        ss.an_msgs.append({"role": "user", "content": q})
        with st.chat_message("user"): st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Querying the comparison…"):
                try:
                    res = analyst.ask(hist, q)
                except Exception as e:
                    res = {"answer": f"Error: {e}", "artifacts": [], "trace": []}
        ss.an_msgs.append({"role": "assistant", "content": res["answer"], "artifacts": res["artifacts"], "trace": res["trace"]})
        st.rerun()
    if ss.an_msgs and st.button("Clear conversation"):
        ss.an_msgs = []; st.rerun()
