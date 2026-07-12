"""
Streamlit UI — Recruiter Outreach Automation.

Talks exclusively to the FastAPI backend (recruiter_outreach.api.main:app);
no business logic lives here. Flow:

  Upload & Send  — upload a file, preview + approve/reject individual
                   rows, then send with a live SSE-streamed progress log.
  Follow-ups     — see who's due for the next touch in the sequence,
                   trigger sending with the same live-progress UI.
  Inbox Tracking — scan for bounces/replies/unsubscribes.
  Suppressions   — view/add/remove the do-not-email list.
  Reports        — deliverability health (bounce/reply rate) + past runs.

Run with: streamlit run frontend/streamlit_app.py
"""

from __future__ import annotations

import json
from typing import Generator, Optional

import pandas as pd
import requests
import streamlit as st

BACKEND_DEFAULT = "http://localhost:8000"

st.set_page_config(page_title="Recruiter Outreach Automation", layout="wide", page_icon="📧")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def backend_url() -> str:
    return st.session_state.get("backend_url", BACKEND_DEFAULT)


def safe_get(path: str, **kwargs) -> Optional[requests.Response]:
    """For calls that fire on every script rerun (not gated behind a
    button click) — a crashed backend must degrade to an inline error
    message, never take down the whole page. Button-triggered calls
    handle their own try/except closer to the user action instead."""
    try:
        return requests.get(f"{backend_url()}{path}", timeout=15, **kwargs)
    except requests.RequestException as exc:
        st.error(f"Could not reach backend at `{backend_url()}`: {exc}")
        return None


def parse_sse_stream(response: requests.Response) -> Generator[tuple[str, dict], None, None]:
    """Yields (event_type, payload_dict) for each SSE block in a streamed
    response. Small hand-rolled parser — the wire format is simple enough
    (event:/data: lines separated by a blank line) that pulling in a
    dedicated SSE client library isn't worth the extra dependency."""
    event_type: Optional[str] = None
    data_lines: list[str] = []

    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            if data_lines:
                try:
                    yield event_type or "message", json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    pass
            event_type, data_lines = None, []
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())

    if data_lines:
        try:
            yield event_type or "message", json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            pass


def render_live_progress(response: requests.Response, total_hint: int = 0) -> dict:
    """Consumes an SSE response, rendering a progress bar + scrolling log
    as events arrive, and returns the final run_complete summary dict
    (empty dict if the run errored before completing)."""
    progress_bar = st.progress(0.0)
    log_placeholder = st.empty()
    logs: list[str] = []
    done = 0
    summary: dict = {}

    icons = {"sent": "✅", "failed": "❌", "skipped": "⚠️", "started": "…"}

    for event_type, payload in parse_sse_stream(response):
        if event_type == "run_complete":
            summary = payload.get("extra") or {}
            progress_bar.progress(1.0)
            if summary.get("blocked_by_send_window"):
                st.warning(
                    f"Send blocked — outside the configured window "
                    f"({summary.get('window', '')}). {summary.get('reason', '')}"
                )
        elif event_type == "error":
            st.error(f"Run failed: {payload.get('reason', 'unknown error')}")
        elif event_type in ("sent", "failed", "skipped"):
            done += 1
            total = payload.get("total") or total_hint or max(done, 1)
            progress_bar.progress(min(done / total, 1.0))
            reason = f" ({payload['reason']})" if payload.get("reason") else ""
            logs.append(f"{icons.get(event_type, '•')} {payload.get('email', '')} — {event_type}{reason}")
            log_placeholder.code("\n".join(logs[-200:]), language=None)
        elif event_type == "started":
            total = payload.get("total") or total_hint or 1
            logs.append(f"{icons['started']} {payload.get('email', '')} — sending…")
            log_placeholder.code("\n".join(logs[-200:]), language=None)

    return summary


# ---------------------------------------------------------------------------
# Sidebar — backend connection + Google account status
# ---------------------------------------------------------------------------

def render_sidebar() -> None:
    st.sidebar.title("📧 Recruiter Outreach")
    st.session_state["backend_url"] = st.sidebar.text_input("Backend URL", value=backend_url())

    try:
        health = requests.get(f"{backend_url()}/health", timeout=5).json()
        st.sidebar.success(f"Backend online — v{health['version']}")
        st.sidebar.caption(f"Send provider: `{health['email_provider']}`")
        st.sidebar.caption(f"Tracking provider: `{health['mail_reader_provider']}`")
    except requests.RequestException:
        st.sidebar.error("Backend unreachable — is uvicorn running?")
        return

    st.sidebar.divider()
    st.sidebar.subheader("Google Account")
    try:
        auth_status = requests.get(f"{backend_url()}/auth/google/status", timeout=5).json()
    except requests.RequestException:
        auth_status = {"connected": False}

    if auth_status.get("connected"):
        st.sidebar.success(f"Connected: {auth_status.get('email', 'unknown')}")
        if st.sidebar.button("Disconnect Gmail"):
            try:
                requests.post(f"{backend_url()}/auth/google/logout", timeout=10)
            except requests.RequestException as exc:
                st.sidebar.error(f"Disconnect failed: {exc}")
            st.rerun()
    else:
        st.sidebar.warning("Gmail not connected")
        st.sidebar.link_button("Connect Gmail", f"{backend_url()}/auth/google/login")
        st.sidebar.caption("Opens Google's consent screen in a new tab.")


# ---------------------------------------------------------------------------
# Tab 1 — Upload & Send
# ---------------------------------------------------------------------------

def render_upload_and_send_tab() -> None:
    st.header("Upload & Send")

    uploaded = st.file_uploader(
        "Recruiter list", type=["csv", "tsv", "xlsx", "xls", "xlsm", "ods", "pdf", "json"],
    )

    if uploaded and st.button("📋 Preview", type="primary"):
        with st.spinner("Loading and validating…"):
            try:
                resp = requests.post(
                    f"{backend_url()}/upload",
                    files={"file": (uploaded.name, uploaded.getvalue())},
                    timeout=60,
                )
            except requests.RequestException as exc:
                st.error(f"Could not reach backend: {exc}")
                resp = None
        if resp is None:
            pass
        elif resp.status_code != 200:
            st.error(resp.json().get("detail", "Upload failed."))
        else:
            st.session_state["preview"] = resp.json()

    preview = st.session_state.get("preview")
    if not preview:
        st.info("Upload a file and click Preview to get started.")
        return

    st.caption(
        f"{preview['total_records']} valid record(s) "
        f"({preview['dropped_rows']} dropped as invalid) — "
        f"preview expires in {preview['expires_in_seconds']}s"
    )

    if preview["send_window_optimal"]:
        st.success(f"🟢 Optimal send window — {preview['send_window_reason']}")
    else:
        st.warning(f"🟡 {preview['send_window_reason']} ({preview['send_window_description']})")

    df = pd.DataFrame(preview["records"])
    df.insert(0, "Send?", True)

    edited = st.data_editor(
        df, use_container_width=True, hide_index=True, key="preview_editor",
        column_config={"Send?": st.column_config.CheckboxColumn(required=True)},
    )

    selected_emails = edited.loc[edited["Send?"], "Email"].tolist()
    st.caption(f"{len(selected_emails)} of {len(edited)} selected to send")

    if st.button("🚀 Approve & Send", type="primary", disabled=not selected_emails):
        try:
            resp = requests.post(
                f"{backend_url()}/send",
                json={"preview_id": preview["preview_id"], "selected_emails": selected_emails},
                stream=True,
                timeout=None,
            )
        except requests.RequestException as exc:
            st.error(f"Could not reach backend: {exc}")
            return
        if resp.status_code != 200:
            st.error(resp.json().get("detail", "Send failed to start."))
            return

        with st.status("Sending…", expanded=True) as status_box:
            summary = render_live_progress(resp, total_hint=len(selected_emails))
            status_box.update(
                label=f"Done — sent={summary.get('sent', 0)} "
                f"failed={summary.get('failed', 0)} skipped={summary.get('skipped', 0)}",
                state="complete",
            )
        st.session_state.pop("preview", None)


# ---------------------------------------------------------------------------
# Tab 2 — Follow-ups
# ---------------------------------------------------------------------------

def render_followups_tab() -> None:
    st.header("Follow-ups")
    st.caption(
        "3-touch sequence (bump → add value → low-pressure close), spaced "
        "per FOLLOWUP_DELAY_DAYS. Run this daily, ideally after Inbox Tracking."
    )

    if st.button("🔍 Check what's due"):
        resp = safe_get("/followups/due")
        st.session_state["followups_due"] = resp.json() if resp is not None and resp.status_code == 200 else None

    due = st.session_state.get("followups_due")
    if due:
        st.metric("Total due", due["total_due"])
        if due["groups"]:
            st.dataframe(pd.DataFrame(due["groups"]), use_container_width=True, hide_index=True)

    if st.button("📨 Send due follow-ups now", type="primary"):
        try:
            resp = requests.post(f"{backend_url()}/followups/run", stream=True, timeout=None)
        except requests.RequestException as exc:
            st.error(f"Could not reach backend: {exc}")
            return
        if resp.status_code != 200:
            st.error("Could not start follow-up run.")
            return
        with st.status("Sending follow-ups…", expanded=True) as status_box:
            summary = render_live_progress(resp)
            status_box.update(
                label=f"Done — sent={summary.get('sent', 0)} "
                f"failed={summary.get('failed', 0)} skipped={summary.get('skipped', 0)}",
                state="complete",
            )


# ---------------------------------------------------------------------------
# Tab 3 — Inbox Tracking
# ---------------------------------------------------------------------------

def render_tracking_tab() -> None:
    st.header("Inbox Tracking")
    st.caption("Scans for bounces, replies, and unsubscribe requests. Run every few hours.")

    since_days = st.number_input("Scan messages from the last N days", min_value=1, max_value=90, value=14)

    if st.button("📬 Check inbox now", type="primary"):
        with st.spinner("Scanning inbox…"):
            try:
                resp = requests.post(
                    f"{backend_url()}/tracking/check", params={"since_days": since_days}, timeout=120,
                )
            except requests.RequestException as exc:
                st.error(f"Could not reach backend: {exc}")
                return
        if resp.status_code != 200:
            st.error(resp.json().get("detail", "Inbox check failed."))
        else:
            stats = resp.json()
            cols = st.columns(4)
            cols[0].metric("Scanned", stats["scanned"])
            cols[1].metric("Bounces", stats["bounces"])
            cols[2].metric("Replies", stats["replies"])
            cols[3].metric("Unsubscribes", stats["unsubscribes"])


# ---------------------------------------------------------------------------
# Tab 4 — Suppressions
# ---------------------------------------------------------------------------

def render_suppressions_tab() -> None:
    st.header("Suppression List")
    st.caption("Addresses that will never be emailed again, regardless of what's in an uploaded file.")

    with st.form("add_suppression", clear_on_submit=True):
        cols = st.columns([2, 2, 1])
        email = cols[0].text_input("Email")
        reason = cols[1].text_input("Reason", value="manual")
        submitted = cols[2].form_submit_button("➕ Add")
        if submitted and email:
            try:
                requests.post(f"{backend_url()}/suppressions", json={"email": email, "reason": reason}, timeout=10)
            except requests.RequestException as exc:
                st.error(f"Could not reach backend: {exc}")
            st.rerun()

    resp = safe_get("/suppressions")
    entries = resp.json() if resp is not None and resp.status_code == 200 else []

    if not entries:
        st.info("No suppressed addresses yet.")
        return

    for entry in entries:
        cols = st.columns([3, 2, 3, 1])
        cols[0].write(entry["email"])
        cols[1].write(entry["reason"])
        cols[2].write(entry["added_at"])
        if cols[3].button("🗑️", key=f"remove-{entry['email']}"):
            try:
                requests.delete(f"{backend_url()}/suppressions/{entry['email']}", timeout=10)
            except requests.RequestException as exc:
                st.error(f"Could not reach backend: {exc}")
            st.rerun()


# ---------------------------------------------------------------------------
# Tab 5 — Reports & Deliverability
# ---------------------------------------------------------------------------

def render_reports_tab() -> None:
    st.header("Reports & Deliverability")

    resp = safe_get("/reports/deliverability")
    if resp is not None and resp.status_code == 200:
        stats = resp.json()
        cols = st.columns(4)
        cols[0].metric("Total sent", stats["total_sent"])
        cols[1].metric("Bounce rate", f"{stats['bounce_rate']:.1%}")
        cols[2].metric("Reply rate", f"{stats['reply_rate']:.1%}")
        cols[3].metric("Total replied", stats["total_replied"])
        if stats["bounce_rate"] > 0.05:
            st.warning(
                "Bounce rate is above 5% — consider pausing sends and reviewing "
                "list quality before continuing (high bounce rates damage sender reputation)."
            )

    st.divider()
    st.subheader("Past runs")
    resp = safe_get("/reports")
    reports = resp.json() if resp is not None and resp.status_code == 200 else []

    if not reports:
        st.info("No run reports yet.")
        return

    st.dataframe(pd.DataFrame(reports), use_container_width=True, hide_index=True)

    filenames = [r["filename"] for r in reports]
    selected = st.selectbox("View a report", filenames)
    if selected:
        detail = safe_get(f"/reports/{selected}")
        if detail is not None and detail.status_code == 200:
            st.download_button("⬇️ Download CSV", detail.text, file_name=selected, mime="text/csv")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("📧 Recruiter Outreach Automation")
    render_sidebar()

    tabs = st.tabs(["📤 Upload & Send", "🔁 Follow-ups", "📬 Inbox Tracking", "🚫 Suppressions", "📊 Reports"])
    with tabs[0]:
        render_upload_and_send_tab()
    with tabs[1]:
        render_followups_tab()
    with tabs[2]:
        render_tracking_tab()
    with tabs[3]:
        render_suppressions_tab()
    with tabs[4]:
        render_reports_tab()


if __name__ == "__main__":
    main()
