import streamlit as st
from core import store, comms, readers
from core.config import INBOX_DIR

def page():
    st.header("2 · Send to vendors & receive replies")
    rfx = store.get("rfx")
    if not rfx:
        st.info("Load or publish an RFx first (step 1)."); return
    st.caption("Transport is stubbed (no real email). Vendors reply however they like - nobody is forced into the template.")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Outbox")
        if st.button(f"Send RFx to {len(rfx['invited_vendors'])} vendors", type="primary"):
            comms.send_rfx(rfx); st.toast("Sent (stub)")
        for e in store.get("outbox", []) or []:
            with st.expander(f"✉️ {e['vendor_id']} · {e['to']} · {e['sent_at']}"):
                st.markdown(f"**Subject:** {e['subject']}"); st.text(e["body"]); st.caption("Attachments: " + ", ".join(e["attachments"]) + " · " + e["transport"])
    with c2:
        st.subheader("Inbox")
        if st.button("Simulate vendor replies (demo files)", type="primary" if store.get("outbox") else "secondary"):
            got = comms.receive_demo_replies(); st.toast(f"Received {sum(len(v) for v in got.values())} files")
        with st.expander("Upload a reply manually"):
            vid = st.selectbox("From vendor", [v["vendor_id"] for v in rfx["invited_vendors"]], format_func=lambda x: f"{x} · {next(v['name'] for v in rfx['invited_vendors'] if v['vendor_id']==x)}")
            ups = st.file_uploader("Any format: xlsx, pdf, docx, eml, jpg/png, txt", accept_multiple_files=True)
            if ups and st.button("Add to inbox"):
                for u in ups: comms.save_upload(vid, u.name, u.getvalue())
                st.toast("Added"); st.rerun()
    st.divider()
    inbox = comms.inbox(); names = {v["vendor_id"]: v["name"] for v in rfx["invited_vendors"]}
    if not inbox:
        st.info("Inbox empty."); return
    for vid, files in inbox.items():
        st.markdown(f"**{names.get(vid, vid)} ({vid})** — {len(files)} file(s)")
        cols = st.columns(max(1, len(files)))
        for col, fn in zip(cols, files):
            p = INBOX_DIR / vid / fn; k = readers.kind_of(p)
            with col:
                st.caption(f"{fn} · {k}")
                with st.popover("Preview"):
                    try:
                        if k == "image": st.image(str(p))
                        elif k == "pdf": st.image(readers.pdf_page_image(p, 1))
                        else: st.code(readers.to_text(p)[:4000], language=None)
                    except Exception as e:
                        st.write(e)
