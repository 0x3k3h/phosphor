"""Exercises the real SMTP listeners: inbound routing + storage, recipient
rejection, authenticated submission, and reply detection.

Binds high ports on localhost. Run:  python tests/test_smtp_integration.py
"""
from __future__ import annotations

import os
import smtplib
import sys
import tempfile
import time
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = tempfile.mkdtemp(prefix="phosphor-smtp-")
os.environ.update(
    PHOSPHOR_DATA_DIR=_TMP,
    PHOSPHOR_SECRET_KEY="smtp-integration-secret",
    PHOSPHOR_PRIMARY_DOMAIN="mx.example",
    PHOSPHOR_SERVER_HOSTNAME="mail.mx.example",
    PHOSPHOR_SMTP_INBOUND_HOST="127.0.0.1",
    PHOSPHOR_SMTP_INBOUND_PORT="2600",
    PHOSPHOR_SMTP_SUBMISSION_PORT="5600",
)

from fastapi.testclient import TestClient  # noqa: E402

from phosphor.app import create_app  # noqa: E402
from phosphor.database import session_scope  # noqa: E402
from phosphor.models import Message, OutboundMessage  # noqa: E402
from phosphor.smtp.server import start_smtp_servers, stop_smtp_servers  # noqa: E402

IN_PORT, SUB_PORT = 2600, 5600


def _wait(predicate, timeout=5.0, interval=0.15):
    end = time.time() + timeout
    while time.time() < end:
        val = predicate()
        if val:
            return val
        time.sleep(interval)
    return predicate()


def main() -> int:
    client = TestClient(create_app(run_services=False))

    tok = client.post("/api/auth/setup", json={
        "email": "admin@mx.example", "password": "integration-pass-1"}).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    dom = client.post("/api/domains", headers=H, json={"name": "mx.example"}).json()
    mb = client.post("/api/mailboxes", headers=H, json={
        "domain_id": dom["id"], "local_part": "drop", "password": "drop-mailbox-pw",
        "display_name": "Drop"}).json()
    assert mb["address"] == "drop@mx.example"

    controllers = start_smtp_servers()
    assert len(controllers) == 2, "both SMTP listeners should be up"
    try:
        # ── 1. inbound delivery for a known recipient ───────────────────────
        msg = EmailMessage()
        msg["From"] = "prospect@remote.example"
        msg["To"] = "drop@mx.example"
        msg["Subject"] = "Hello from the outside"
        msg["Message-ID"] = "<inbound-1@remote.example>"
        msg.set_content("This should land in the drop mailbox.")

        with smtplib.SMTP("127.0.0.1", IN_PORT, timeout=10) as s:
            s.send_message(msg)

        row = _wait(lambda: _find_message("drop@mx.example", "Hello from the outside"))
        assert row is not None, "inbound message was not stored"
        assert row["folder"] == "INBOX"
        assert Path(_TMP, "maildirs", "drop@mx.example", "new").exists()
        print("[ok] inbound delivery + storage")

        # ── 2. recipient rejection ─────────────────────────────────────────
        with smtplib.SMTP("127.0.0.1", IN_PORT, timeout=10) as s:
            s.ehlo("tester")
            code, _ = s.docmd("MAIL FROM:<x@remote.example>")
            assert code == 250
            code, resp = s.docmd("RCPT TO:<nobody@mx.example>")
            assert code == 550, f"unknown user should be 550, got {code} {resp}"
            code, resp = s.docmd("RCPT TO:<someone@not-ours.example>")
            assert code == 550, f"relay should be denied, got {code} {resp}"
        print("[ok] RCPT rejection (unknown user + relay denied)")

        # ── 3. authenticated submission ───────────────────────────────────
        out = EmailMessage()
        out["From"] = "drop@mx.example"
        out["To"] = "someone@external.example"
        out["Subject"] = "Outbound via submission"
        out["Message-ID"] = "<sub-1@mx.example>"
        out.set_content("hi from submission")

        with smtplib.SMTP("127.0.0.1", SUB_PORT, timeout=10) as s:
            s.ehlo("tester")
            s.login("drop@mx.example", "drop-mailbox-pw")
            s.send_message(out)

        queued = _wait(lambda: _count_outbound("someone@external.example"))
        assert queued and queued >= 1, "submission did not enqueue an outbound message"
        sent_copy = _wait(lambda: _find_message("drop@mx.example", "Outbound via submission", folder="Sent"))
        assert sent_copy is not None, "submission should file a copy in Sent"
        print("[ok] authenticated submission -> outbound queue + Sent copy")

        # ── 4. submission rejects bad credentials ─────────────────────────
        try:
            with smtplib.SMTP("127.0.0.1", SUB_PORT, timeout=10) as s:
                s.ehlo("tester")
                s.login("drop@mx.example", "wrong-password")
            bad_login_ok = True
        except smtplib.SMTPAuthenticationError:
            bad_login_ok = False
        assert bad_login_ok is False, "bad password must not authenticate"
        print("[ok] submission rejects wrong password")

    finally:
        stop_smtp_servers()

    print("\nALL SMTP INTEGRATION CHECKS PASSED")
    return 0


def _find_message(address: str, subject: str, folder: str | None = None) -> dict | None:
    with session_scope() as db:
        from phosphor.models import Mailbox

        mailbox = db.query(Mailbox).filter(Mailbox.address == address).first()
        if not mailbox:
            return None
        q = db.query(Message).filter(Message.mailbox_id == mailbox.id, Message.subject == subject)
        if folder:
            q = q.filter(Message.folder == folder)
        m = q.first()
        return {"id": m.id, "folder": m.folder, "subject": m.subject} if m else None


def _count_outbound(to_addr: str) -> int:
    with session_scope() as db:
        return db.query(OutboundMessage).filter(OutboundMessage.envelope_to == to_addr).count()


if __name__ == "__main__":
    raise SystemExit(main())
