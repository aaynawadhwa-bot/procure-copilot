import json, time
import pandas as pd
import streamlit as st
from core import store, extract, normalize, llm, comms
from ui.common import table, show_evidence, vlabel, SEV_ICON, inr

def run_extraction(rfx, vendors, force=False):
    errors = {}
    with st.status("Reading vendor responses with Claude…", expanded=True) as s:
        for v in vendors:
            if not extract.vendor_files(v["vendor_id"]):
                st.write(f"⏭ {v['name']}: no files"); continue
            t0 = time.time(); st.write(f"📄 {v['name']}: {', '.join(f.name for f in extract.vendor_files(v['vendor_id']))}")
            try:
                rec = extract.extract_vendor(rfx, v, force=force)
                n = sum(1 for l in rec["result"].get("line_quotes", []) if l.get("status") == "quoted")
                st.write(f"✅ {v['name']}: {n} lines read, {len(rec['result'].get('questionnaire', []))} answers, {len(rec['result'].get('documents', []))} documents ({time.time()-t0:.0f}s)")
            except Exception as e:
                import traceback; traceback.print_exc()
                errors[v["vendor_id"]] = f"{v['name']}: {e}"
                st.write(f"❌ {v['name']}: {e}")
        store.put("extract_errors", errors)
        st.write("🧮 Normalising units, currency, freight; verifying citations; scoring confidence; evaluating quality gate…")
        try:
            normalize.rebuild()
        except Exception as e:
            import traceback; traceback.print_exc()
            errors["_normalise"] = f"Normalisation step failed: {e}"; store.put("extract_errors", errors)
        s.update(label="Extraction finished with errors" if errors else "Extraction complete", state="error" if errors else "complete")

def page():
    st.header("3 · Extract & review")
    rfx = store.get("rfx")
    if not rfx: st.info("Load an RFx first."); return
    vendors = rfx["invited_vendors"]
    have = [v for v in vendors if extract.vendor_files(v["vendor_id"])]
    c = st.columns([1.3, 1, 2.2])
    if c[0].button(f"Run AI extraction ({len(have)} vendors)", type="primary", disabled=not have or not llm.available()):
        run_extraction(rfx, have); st.rerun()
    with c[1].popover("Re-run one vendor"):
        vv = st.selectbox("Vendor", [v["vendor_id"] for v in have], format_func=vlabel) if have else None
        if vv and st.button("Re-extract (ignore cache)"):
            run_extraction(rfx, [v for v in have if v["vendor_id"] == vv], force=True); st.rerun()
    if not llm.available(): c[2].warning("ANTHROPIC_API_KEY not set - extraction disabled.")
    elif not have: c[2].info("No vendor files yet - go to step 2 and receive replies.")
    errs = store.get("extract_errors", {}) or {}
    for k, e in errs.items():
        st.error(f"Last extraction problem - {e}")
    if errs:
        st.caption("Fix the cause above, then click Run AI extraction again. Vendors that succeeded are cached and won't be re-billed.")
    q = table("quotes")
    if q.empty:
        if not errs and have:
            st.info("No extraction results yet. Click **Run AI extraction**.")
        return
    v = table("vendors"); f = table("flags")
    st.subheader("What the system read")
    m = st.columns(len(v))
    for col, r in zip(m, v.itertuples()):
        crit = int(((f.vendor_id == r.vendor_id) & (f.severity == "critical") & (f.resolved == 0)).sum())
        col.metric(vlabel(r.vendor_id), f"{int(r.lines_priced or 0)}/{int(r.lines_total)} lines", f"{int(r.needs_review or 0)} to review", delta_color="inverse" if r.needs_review else "off")
        col.caption(f"{r.currency} · {r.price_basis} · freight {r.freight_type}" + (f" · 🔴 {crit} critical" if crit else ""))
    tab1, tab2, tab3 = st.tabs(["Review queue", "Vendor-level issues & clarifications", "Raw extraction"])
    with tab1:
        need = q[q.review_status == "needs_review"].copy()
        sev = f[(f.resolved == 0) & f.line_id.notna()].copy()
        sev["rank"] = sev.severity.map({"critical": 0, "review": 1, "info": 2})
        top = sev.sort_values("rank").groupby(["vendor_id", "line_id"]).first().reset_index()[["vendor_id", "line_id", "severity", "message"]]
        need = need.merge(top, on=["vendor_id", "line_id"], how="left")
        crit_unres = q[q.status == "unresolvable"].merge(top, on=["vendor_id", "line_id"], how="left")
        queue = pd.concat([crit_unres, need]).drop_duplicates(["vendor_id", "line_id"])
        st.caption(f"{len(queue)} items need a human decision. Nothing here is silently guessed: fix, confirm or exclude.")
        if queue.empty:
            st.success("Review queue is empty."); return
        queue["item"] = queue.apply(lambda r: f"{SEV_ICON.get(r.severity,'')} {r.vendor_id} · {r.line_id} · {str(r.message)[:90]}", axis=1)
        st.dataframe(queue[["vendor_id", "line_id", "status", "raw_price", "raw_unit", "landed_inr_per_uom", "confidence", "severity", "message"]],
                     hide_index=True, use_container_width=True, height=260)
        pick = st.selectbox("Open item", queue["item"].tolist())
        r = queue[queue["item"] == pick].iloc[0]
        show_evidence(r.vendor_id, r.line_id)
        st.markdown("**Decision**")
        a1, a2, a3 = st.columns([1, 2, 1])
        reason = st.text_input("Reason / note (goes to audit log)", key=f"rs_{r.vendor_id}_{r.line_id}")
        ov = store.get("overrides", {}) or {}; k = f"{r.vendor_id}|{r.line_id}"
        if a1.button("✅ Confirm as read", key=f"cf_{k}"):
            ov[k] = {"action": "confirm", "reason": reason, "by": "buyer", "ts": time.strftime("%Y-%m-%d %H:%M")}
            store.put("overrides", ov); store.log("override", {"key": k, **ov[k]}, actor="buyer"); normalize.rebuild(); st.rerun()
        with a2:
            p = st.number_input("Correct price (INR per RFx UoM, before freight)", min_value=0.0, value=float(r.price_inr_per_uom or 0), step=0.05, key=f"np_{k}")
            fr = st.number_input("Freight INR per UoM", min_value=0.0, value=float(r.freight_inr_per_uom or 0), step=0.05, key=f"nf_{k}")
            if st.button("✏️ Set corrected price", key=f"sp_{k}"):
                ov[k] = {"action": "set_price", "price_inr_per_uom": p, "freight_inr_per_uom": fr, "reason": reason or "buyer correction", "by": "buyer", "ts": time.strftime("%Y-%m-%d %H:%M")}
                store.put("overrides", ov); store.log("override", {"key": k, **ov[k]}, actor="buyer"); normalize.rebuild(); st.rerun()
        if a3.button("🚫 Exclude from award", key=f"ex_{k}"):
            ov[k] = {"action": "exclude", "reason": reason, "by": "buyer", "ts": time.strftime("%Y-%m-%d %H:%M")}
            store.put("overrides", ov); store.log("override", {"key": k, **ov[k]}, actor="buyer"); normalize.rebuild(); st.rerun()
    with tab2:
        for vr in v.itertuples():
            vf = f[(f.vendor_id == vr.vendor_id)]
            issues = vf[vf.severity.isin(["critical", "review"]) & (vf.resolved == 0)]
            with st.expander(f"{vlabel(vr.vendor_id)} — {len(issues)} open issues · gate: {vr.gate_status}"):
                for x in vf[vf.line_id.isna()].itertuples():
                    st.markdown(f"{SEV_ICON.get(x.severity,'')} {x.message}")
                if len(issues):
                    st.caption(f"{len(issues[issues.line_id.notna()])} line-level issues (see review queue)")
                    if st.button("✍️ Draft clarification email", key=f"cl_{vr.vendor_id}", disabled=not llm.available()):
                        vend = next(x for x in rfx["invited_vendors"] if x["vendor_id"] == vr.vendor_id)
                        with st.spinner("Drafting…"):
                            st.session_state[f"clar_{vr.vendor_id}"] = comms.draft_clarification(rfx, vend, issues.head(25).to_dict("records"))
                    if st.session_state.get(f"clar_{vr.vendor_id}"):
                        st.text_area("Draft (edit before sending)", st.session_state[f"clar_{vr.vendor_id}"], height=260, key=f"ta_{vr.vendor_id}")
    with tab3:
        vv = st.selectbox("Vendor", v.vendor_id.tolist(), format_func=vlabel, key="rawv")
        rec = store.get(f"extraction:{vv}") or {}
        st.caption(f"Model {rec.get('model')} · prompt {rec.get('prompt_version')} · {rec.get('run_at')} · files: {', '.join(rec.get('files', []))}")
        st.json(rec.get("result", {}), expanded=False)
