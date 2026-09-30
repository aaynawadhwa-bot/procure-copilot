import json
import pandas as pd
import streamlit as st
from core import store, copilot, llm
from core.rfx import load_demo_rfx, save_rfx

STARTERS = ["I need an annual rate contract for about 30 corrugated boxes for our Bhiwandi warehouse - 3, 5 and 7 ply, some printed mailers. Start me off.",
            "Add a quality questionnaire with ISO 9001, capacity, lab, lead time and moisture as knock-outs.",
            "Invite Shivam Corrupack, Pacific Fibreboard, Deccan Corrugated, Rathi Kraft and Om Sai (our incumbent)."]

def page():
    st.header("1 · Create the RFx with the co-pilot")
    st.caption("Talk the RFx into existence. The co-pilot keeps a structured draft (lines, specs, questionnaire, terms) that you can edit and publish.")
    ss = st.session_state
    ss.setdefault("cp_msgs", []); ss.setdefault("cp_draft", None)
    live = store.get("rfx")
    top = st.columns([1, 1, 2])
    if top[0].button("Load demo RFx (NHPC-047, 30 lines)", type="primary" if not live else "secondary"):
        save_rfx(load_demo_rfx()); ss.cp_draft = store.get("rfx"); st.toast("Demo RFx loaded as the live RFx"); st.rerun()
    if ss.cp_draft and top[1].button("Publish draft as live RFx"):
        d = dict(ss.cp_draft); base = load_demo_rfx()
        for k in ("rfx_no", "issued", "due", "price_basis", "payment_terms", "buyer", "invited_vendors"):
            d.setdefault(k, base.get(k))
        d.setdefault("price_validity_required_days", 180); d.setdefault("questionnaire", [])
        save_rfx(d); st.toast("Published"); st.rerun()
    if live:
        top[2].success(f"Live RFx: **{live['rfx_no']}** · {len(live['line_items'])} lines · {len(live['questionnaire'])} questions · {len(live.get('invited_vendors', []))} vendors")
    left, right = st.columns([1, 1.25])
    with left:
        st.subheader("Co-pilot")
        if not llm.available():
            st.warning("Set ANTHROPIC_API_KEY in .env to chat. You can still load the demo RFx.")
        box = st.container(height=480)
        for m in ss.cp_msgs:
            box.chat_message(m["role"]).markdown(m["content"])
        if not ss.cp_msgs:
            for i, s in enumerate(STARTERS):
                if st.button(s, key=f"starter{i}"):
                    ss._cp_pending = s; st.rerun()
        msg = st.chat_input("Describe what you need to buy…")
        msg = msg or ss.pop("_cp_pending", None)
        if msg and llm.available():
            ss.cp_msgs.append({"role": "user", "content": msg})
            with st.spinner("Drafting…"):
                try:
                    reply, draft = copilot.turn(ss.cp_msgs[:-1], msg, ss.cp_draft)
                except Exception as e:
                    reply, draft = f"Error: {e}", ss.cp_draft
            ss.cp_draft = draft; ss.cp_msgs.append({"role": "assistant", "content": reply}); st.rerun()
    with right:
        st.subheader("Draft")
        d = ss.cp_draft or live
        if not d:
            st.info("No draft yet."); return
        st.markdown(f"**{d.get('title','(untitled)')}**  \n{d.get('rfx_no','')} · due {d.get('due','?')} · {d.get('price_basis','')}")
        t1, t2, t3, t4 = st.tabs([f"Lines ({len(d.get('line_items', []))})", f"Questionnaire ({len(d.get('questionnaire', []))})", "Terms", "Vendors"])
        with t1:
            cols = ["line_id", "material_code", "description", "annual_qty", "uom", "est_weight_kg"]
            df = pd.DataFrame(d.get("line_items", []))
            if not df.empty:
                st.dataframe(df[[c for c in cols if c in df.columns]], hide_index=True, use_container_width=True, height=380)
        with t2:
            q = pd.DataFrame(d.get("questionnaire", []))
            if not q.empty:
                st.dataframe(q[[c for c in ["id", "question", "knock_out", "criterion"] if c in q.columns]], hide_index=True, use_container_width=True)
            if d.get("quality_gate_rule"): st.caption(d["quality_gate_rule"])
        with t3:
            for k in ("price_basis", "payment_terms", "price_validity_required_days", "contract_period", "fx_policy"):
                if d.get(k): st.markdown(f"- **{k.replace('_', ' ')}**: {d[k]}")
            for t in d.get("terms", []) or []: st.markdown(f"- {t}")
        with t4:
            st.dataframe(pd.DataFrame(d.get("invited_vendors", [])), hide_index=True, use_container_width=True)
        if d.get("open_questions"):
            st.warning("Co-pilot still needs: " + "; ".join(d["open_questions"]))
