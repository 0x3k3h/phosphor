"""End-to-end smoke test that exercises the main API flows without binding
any real network ports. Run with:  pytest -q   (or  python tests/test_smoke.py )
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Isolate data into a temp dir BEFORE importing the app.
_TMP = tempfile.mkdtemp(prefix="phosphor-test-")
os.environ["PHOSPHOR_DATA_DIR"] = _TMP
os.environ["PHOSPHOR_SECRET_KEY"] = "test-secret-key-not-for-production-use"
os.environ["PHOSPHOR_PRIMARY_DOMAIN"] = "test.example"
os.environ["PHOSPHOR_SERVER_HOSTNAME"] = "mail.test.example"

from fastapi.testclient import TestClient  # noqa: E402

from phosphor.app import create_app  # noqa: E402
from phosphor.config import settings  # noqa: E402
from phosphor.outreach import personalization as pz  # noqa: E402
from phosphor.crypto.dkim import generate_keypair, sign_message  # noqa: E402
from phosphor.outreach.mime_builder import build_message  # noqa: E402


def _client() -> TestClient:
    app = create_app(run_services=False)
    return TestClient(app)


def test_personalization():
    from phosphor.models import Contact

    c = Contact(email="a@b.com", first_name="Sam", company="", custom={"industry": "SaaS"})
    ctx = pz.context_for_contact(c)
    assert pz.render("Hi {{first_name}}", ctx) == "Hi Sam"
    assert pz.render("Hi {{first_name|there}}", ctx) == "Hi Sam"
    assert pz.render("At {{company|your company}}", ctx) == "At your company"
    assert pz.render("{{industry}} outreach", ctx) == "SaaS outreach"
    assert pz.missing_tags("Hey {{last_name}}", ctx) == ["last_name"]
    # spintax is deterministic per seed
    out1 = pz.render("{a|b|c} test", ctx, seed="x")
    out2 = pz.render("{a|b|c} test", ctx, seed="x")
    assert out1 == out2 and out1.split()[0] in {"a", "b", "c"}


def test_dkim_sign_roundtrip():
    priv, pub = generate_keypair()
    assert "PRIVATE KEY" in priv and len(pub) > 100
    raw, mid = build_message(
        from_addr="x@test.example", to=["y@remote.example"],
        subject="hi", body_text="hello world",
    )
    signed = sign_message(raw, domain="test.example", selector="phosphor", private_pem=priv)
    assert signed.lower().startswith(b"dkim-signature:")
    assert raw in signed


def test_full_api_flow():
    client = _client()

    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/auth/status").json()["needs_setup"] is True

    r = client.post("/api/auth/setup", json={
        "email": "admin@test.example", "password": "supersecret123", "display_name": "Admin",
    })
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/auth/status").json()["needs_setup"] is False
    assert client.get("/api/auth/me", headers=H).json()["email"] == "admin@test.example"

    # ── domain + DKIM + DNS records
    r = client.post("/api/domains", headers=H, json={"name": "test.example", "dkim_selector": "phosphor"})
    assert r.status_code == 201, r.text
    dom = r.json()
    assert dom["dkim_txt_value"].startswith("v=DKIM1")

    dns = client.get(f"/api/domains/{dom['id']}/dns", headers=H).json()
    kinds = {rec["kind"] for rec in dns["records"]}
    assert {"mx", "spf", "dkim", "dmarc", "ptr"} <= kinds

    # ── mailbox
    r = client.post("/api/mailboxes", headers=H, json={
        "domain_id": dom["id"], "local_part": "outreach", "password": "mailboxpass1",
        "display_name": "Outreach", "quota_mb": 512,
    })
    assert r.status_code == 201, r.text
    mailbox = r.json()
    assert mailbox["address"] == "outreach@test.example"
    assert Path(mailbox and settings.maildir_root / "outreach@test.example").exists()

    # ── contacts via CSV import
    csv_bytes = (
        "Email,First Name,Company,Job Title,Industry\n"
        "jordan@acme.example,Jordan,Acme,Head of Growth,SaaS\n"
        "casey@beta.example,Casey,Beta LLC,Founder,Fintech\n"
        "not-an-email,Broken,,,\n"
    ).encode()
    r = client.post(
        "/api/contacts/import?list_name=Q3%20Test",
        headers=H,
        files={"file": ("contacts.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert r.status_code == 200, r.text
    imp = r.json()
    assert imp["created"] == 2 and imp["skipped"] == 1
    list_id = imp["list_id"]

    contacts = client.get("/api/contacts", headers=H).json()
    assert len(contacts) == 2
    jordan = next(c for c in contacts if c["email"] == "jordan@acme.example")
    assert jordan["custom"].get("industry") == "SaaS"

    # ── template + live preview
    r = client.post("/api/templates", headers=H, json={
        "name": "Intro", "subject": "Quick question about {{company}}",
        "body_html": "<p>Hi {{first_name|there}}, saw you're {{title|leading things}} at {{company}}.</p>",
        "body_text": "",
    })
    assert r.status_code == 201, r.text
    prev = client.post("/api/templates/preview", headers=H, json={
        "subject": "Quick question about {{company}}",
        "body_html": "<p>Hi {{first_name|there}} — {{missingtag}}</p>",
    }).json()
    assert "Acme" in prev["subject"]
    assert "missingtag" in prev["missing_tags"]

    # ── campaign + sequence + enrol
    r = client.post("/api/campaigns", headers=H, json={
        "name": "Q3 Outbound", "from_mailbox_id": mailbox["id"], "list_id": list_id,
        "daily_cap": 5, "min_delay_seconds": 1, "max_delay_seconds": 2,
        # 24/7 window so the test is wall-clock independent
        "window_start_hour": 0, "window_end_hour": 0,
        "send_days": [0, 1, 2, 3, 4, 5, 6],
    })
    assert r.status_code == 201, r.text
    camp = r.json()

    r = client.put(f"/api/campaigns/{camp['id']}/steps", headers=H, json=[
        {"step_order": 1, "subject": "Quick question about {{company}}",
         "body_html": "<p>Hi {{first_name|there}}</p>", "body_text": "", "wait_days": 0,
         "condition": "always", "same_thread": True},
        {"step_order": 2, "subject": "", "body_html": "<p>Bumping this {{first_name}}</p>",
         "body_text": "", "wait_days": 3, "condition": "if_no_reply", "same_thread": True},
    ])
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2

    r = client.post(f"/api/campaigns/{camp['id']}/enroll", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["recipients_total"] == 2

    r = client.post(f"/api/campaigns/{camp['id']}/start", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"

    # Drive one sequencing tick by hand (worker is not running in tests).
    from phosphor.database import session_scope
    from phosphor.outreach import campaigns as camp_engine

    with session_scope() as db:
        summary = camp_engine.tick(db)
    assert summary["sent"] >= 1

    stats = client.get(f"/api/campaigns/{camp['id']}/stats", headers=H).json()
    assert stats["sent"] >= 1
    assert stats["recipients_total"] == 2

    # An outbound row should now be spooled and queued.
    from phosphor.database import session_scope as _s
    from phosphor.models import OutboundMessage

    with _s() as db:
        q = db.query(OutboundMessage).all()
        assert len(q) >= 1
        assert Path(q[0].raw_path).exists()

    # ── suppression / unsubscribe token
    from phosphor.outreach.campaigns import unsubscribe_token, verify_unsubscribe_token

    tok = unsubscribe_token(camp["id"], jordan["id"])
    assert verify_unsubscribe_token(tok) == (camp["id"], jordan["id"])
    assert verify_unsubscribe_token("garbage") is None

    r = client.post("/api/suppressions", headers=H, json={"email": "casey@beta.example", "reason": "manual"})
    assert r.status_code == 201
    supp = client.get("/api/suppressions", headers=H).json()
    assert any(s["email"] == "casey@beta.example" for s in supp)

    # ── webmail: compose queues + lands in Sent
    r = client.post(f"/api/mail/{mailbox['id']}/send", headers=H, json={
        "from_mailbox_id": mailbox["id"], "to": ["someone@remote.example"],
        "subject": "Hello", "body_text": "hi there", "body_html": "",
    })
    assert r.status_code == 200, r.text
    folders = client.get(f"/api/mail/{mailbox['id']}/folders", headers=H).json()
    assert folders["Sent"]["total"] >= 1

    dash = client.get("/api/dashboard/stats", headers=H).json()
    assert dash["domains"] == 1 and dash["mailboxes"] == 1 and dash["contacts"] == 2

    print("OK — full API flow passed")


if __name__ == "__main__":
    test_personalization()
    test_dkim_sign_roundtrip()
    test_full_api_flow()
    print("all smoke tests passed")
