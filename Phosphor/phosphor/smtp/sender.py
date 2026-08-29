"""Outbound delivery worker.

Reads `outbound_messages` rows that are due, resolves the recipient's MX,
DKIM-signs (when we hold a key for the envelope-from domain) and delivers over
SMTP with opportunistic STARTTLS. Retries on 4xx with capped backoff; treats
5xx / repeated failure as a hard bounce.
"""
from __future__ import annotations

import datetime as dt
import logging
import smtplib
import socket
import ssl
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path

import dns.exception
import dns.resolver
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..crypto.dkim import sign_message
from ..models import (
    Campaign,
    CampaignRecipient,
    Domain,
    EmailEvent,
    EventType,
    OutboundMessage,
    OutboundStatus,
    RecipientStatus,
    utcnow,
)
from ..outreach.suppression import suppress

log = logging.getLogger("phosphor.sender")

# Backoff per attempt number (minutes). After the list is exhausted -> FAILED.
_BACKOFF_MIN = [1, 5, 15, 60, 180, 360, 720, 1440]
_resolver = dns.resolver.Resolver()
_resolver.timeout = 5
_resolver.lifetime = 10


class DeliveryResult:
    def __init__(self, ok: bool, *, permanent: bool = False, detail: str = ""):
        self.ok = ok
        self.permanent = permanent
        self.detail = detail


def _mx_for(domain: str) -> list[str]:
    try:
        answers = _resolver.resolve(domain, "MX")
        hosts = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
        return [h for _p, h in hosts]
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        # Fall back to an implicit A record per RFC 5321 §5.1
        return [domain]
    except dns.exception.DNSException as exc:
        log.warning("MX lookup failed for %s: %s", domain, exc)
        return []


def _maybe_sign(db: Session, raw: bytes, envelope_from: str) -> bytes:
    domain_name = envelope_from.split("@", 1)[-1].lower()
    domain = db.scalar(select(Domain).where(Domain.name == domain_name))
    if not domain or not domain.dkim_private_key:
        return raw
    try:
        return sign_message(
            raw, domain=domain.name,
            selector=domain.dkim_selector or settings.dkim_selector,
            private_pem=domain.dkim_private_key,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("DKIM signing failed for %s: %s", domain_name, exc)
        return raw


def _smtp_send(host: str, mail_from: str, rcpt: str, raw: bytes) -> DeliveryResult:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with smtplib.SMTP(host, 25, timeout=30, local_hostname=settings.server_hostname) as smtp:
            smtp.ehlo(settings.server_hostname)
            if smtp.has_extn("starttls"):
                smtp.starttls(context=ctx)
                smtp.ehlo(settings.server_hostname)
            code, resp = smtp.mail(mail_from)
            if code >= 400:
                return DeliveryResult(False, permanent=code >= 500, detail=f"MAIL {code} {resp!r}")
            code, resp = smtp.rcpt(rcpt)
            if code >= 400:
                return DeliveryResult(False, permanent=code >= 500, detail=f"RCPT {code} {resp!r}")
            code, resp = smtp.data(raw)
            if code >= 400:
                return DeliveryResult(False, permanent=code >= 500, detail=f"DATA {code} {resp!r}")
            return DeliveryResult(True, detail=f"{code} {resp!r}")
    except smtplib.SMTPResponseException as exc:
        return DeliveryResult(False, permanent=exc.smtp_code >= 500, detail=f"{exc.smtp_code} {exc.smtp_error!r}")
    except (smtplib.SMTPException, socket.error, ssl.SSLError, OSError) as exc:
        return DeliveryResult(False, permanent=False, detail=f"{type(exc).__name__}: {exc}")


def deliver_one(db: Session, ob: OutboundMessage) -> None:
    raw_path = Path(ob.raw_path)
    if not raw_path.exists():
        ob.status = OutboundStatus.FAILED.value
        ob.last_error = "spool file missing"
        db.flush()
        return

    raw = raw_path.read_bytes()
    raw = _maybe_sign(db, raw, ob.envelope_from)

    rcpt_domain = ob.envelope_to.split("@", 1)[-1].lower()
    hosts = _mx_for(rcpt_domain)
    if not hosts:
        _handle_failure(db, ob, DeliveryResult(False, permanent=False, detail="no MX / DNS error"))
        return

    last: DeliveryResult | None = None
    for host in hosts[:3]:
        last = _smtp_send(host, ob.envelope_from, ob.envelope_to, raw)
        log.info("deliver id=%s to=%s via=%s ok=%s detail=%s", ob.id, ob.envelope_to, host, last.ok, last.detail)
        if last.ok or last.permanent:
            break

    if last and last.ok:
        ob.status = OutboundStatus.SENT.value
        ob.sent_at = utcnow()
        ob.last_error = ""
        _log_campaign_event(db, ob, EventType.DELIVERED.value, {"detail": last.detail})
        try:
            raw_path.unlink()
        except OSError:
            pass
    else:
        _handle_failure(db, ob, last or DeliveryResult(False, detail="unknown"))
    db.flush()


def _handle_failure(db: Session, ob: OutboundMessage, result: DeliveryResult) -> None:
    ob.attempts += 1
    ob.last_error = result.detail[:1000]
    if result.permanent or ob.attempts > len(_BACKOFF_MIN):
        ob.status = OutboundStatus.FAILED.value
        _log_campaign_event(db, ob, EventType.BOUNCE.value, {"detail": result.detail, "hard": result.permanent})
        if ob.campaign_id and ob.contact_id:
            from ..models import Contact
            contact = db.get(Contact, ob.contact_id)
            if contact:
                suppress(db, contact.email, reason="bounce", campaign_id=ob.campaign_id,
                         note=result.detail[:200])
            rcpt = db.scalar(select(CampaignRecipient).where(
                CampaignRecipient.campaign_id == ob.campaign_id,
                CampaignRecipient.contact_id == ob.contact_id,
            ))
            if rcpt:
                rcpt.status = RecipientStatus.STOPPED_BOUNCED.value
                rcpt.next_action_at = None
    else:
        ob.status = OutboundStatus.DEFERRED.value
        delay = _BACKOFF_MIN[min(ob.attempts - 1, len(_BACKOFF_MIN) - 1)]
        ob.next_attempt_at = utcnow() + dt.timedelta(minutes=delay)


def _log_campaign_event(db: Session, ob: OutboundMessage, event_type: str, meta: dict) -> None:
    if not ob.campaign_id:
        return
    db.add(EmailEvent(
        type=event_type, campaign_id=ob.campaign_id, contact_id=ob.contact_id,
        outbound_message_id=ob.id, meta=meta, created_at=utcnow(),
    ))


def process_queue(db: Session, *, limit: int = 20) -> dict:
    now = utcnow()
    due = db.scalars(
        select(OutboundMessage)
        .where(
            OutboundMessage.status.in_([OutboundStatus.QUEUED.value, OutboundStatus.DEFERRED.value]),
            OutboundMessage.next_attempt_at <= now,
        )
        .order_by(OutboundMessage.next_attempt_at)
        .limit(limit)
    ).all()

    counters = {"attempted": 0, "sent": 0, "deferred": 0, "failed": 0}
    for ob in due:
        ob.status = OutboundStatus.SENDING.value
        db.flush()
        try:
            deliver_one(db, ob)
        except Exception as exc:  # noqa: BLE001
            log.exception("deliver_one crashed for id=%s", ob.id)
            ob.status = OutboundStatus.DEFERRED.value
            ob.attempts += 1
            ob.last_error = f"crash: {exc}"
            ob.next_attempt_at = utcnow() + dt.timedelta(minutes=15)

        counters["attempted"] += 1
        if ob.status == OutboundStatus.SENT.value:
            counters["sent"] += 1
        elif ob.status == OutboundStatus.FAILED.value:
            counters["failed"] += 1
        else:
            counters["deferred"] += 1
        db.commit()
    return counters
