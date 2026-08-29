"""Inbound MX listener (port 25) and authenticated Submission listener (587).

Both run in their own threads via aiosmtpd Controllers. DB work uses the sync
`session_scope()` — fine at self-hosted volume.
"""
from __future__ import annotations

import logging
import ssl
from email.utils import parseaddr

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPProtocol
from aiosmtpd.smtp import AuthResult, LoginPassword
from sqlalchemy import select

from ..config import settings
from ..crypto.passwords import verify_password
from ..database import session_scope
from ..mailstore import MailStore, parse_message
from ..models import (
    Alias,
    Domain,
    Mailbox,
    Message,
    OutboundMessage,
    OutboundStatus,
    utcnow,
)
from . import spam
from .reply_detect import classify_inbound

log = logging.getLogger("phosphor.smtp")

_MAX_RCPT = 100


# ─────────────────────────────────────────────────────────────────────────────
#  Routing helpers
# ─────────────────────────────────────────────────────────────────────────────
def _split(addr: str) -> tuple[str, str]:
    addr = parseaddr(addr)[1].lower()
    if "@" not in addr:
        return addr, ""
    local, domain = addr.rsplit("@", 1)
    return local, domain


def _resolve_local_targets(db, rcpt: str) -> list[Mailbox]:
    """Follow mailboxes / aliases / catch-all to a set of local mailboxes.
    Returns [] if the address is not deliverable here."""
    local, domain_name = _split(rcpt)
    domain = db.scalar(select(Domain).where(Domain.name == domain_name, Domain.active.is_(True)))
    if domain is None:
        return []

    mailbox = db.scalar(select(Mailbox).where(Mailbox.address == f"{local}@{domain_name}", Mailbox.active.is_(True)))
    if mailbox:
        return [mailbox]

    aliases = db.scalars(
        select(Alias).where(
            Alias.domain_id == domain.id,
            Alias.active.is_(True),
            Alias.source.in_([f"{local}@{domain_name}", f"@{domain_name}"]),
        )
    ).all()
    targets: list[Mailbox] = []
    forwards: list[str] = []
    for alias in aliases:
        d_local, d_domain = _split(alias.destination)
        mb = db.scalar(select(Mailbox).where(Mailbox.address == f"{d_local}@{d_domain}", Mailbox.active.is_(True)))
        if mb:
            targets.append(mb)
        else:
            forwards.append(alias.destination)
    if forwards:
        # Stash external forwards on the object for the caller. Simplest: attach.
        _resolve_local_targets.last_forwards = forwards  # type: ignore[attr-defined]
    else:
        _resolve_local_targets.last_forwards = []  # type: ignore[attr-defined]
    if targets:
        return targets

    if domain.catch_all_mailbox_id:
        mb = db.get(Mailbox, domain.catch_all_mailbox_id)
        if mb and mb.active:
            return [mb]
    return []


def _is_our_domain(db, domain_name: str) -> bool:
    return db.scalar(select(Domain.id).where(Domain.name == domain_name, Domain.active.is_(True))) is not None


# ─────────────────────────────────────────────────────────────────────────────
#  Inbound MX handler
# ─────────────────────────────────────────────────────────────────────────────
class InboundHandler:
    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: ANN001,N802
        _local, domain_name = _split(address)
        with session_scope() as db:
            if not _is_our_domain(db, domain_name):
                return "550 5.7.1 Relay access denied"
            targets = _resolve_local_targets(db, address)
            forwards = getattr(_resolve_local_targets, "last_forwards", [])
            if not targets and not forwards:
                return "550 5.1.1 No such user here"
        if len(envelope.rcpt_tos) >= _MAX_RCPT:
            return "452 4.5.3 Too many recipients"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):  # noqa: ANN001,N802
        raw: bytes = envelope.content if isinstance(envelope.content, bytes) else envelope.content.encode()
        if len(raw) > settings.max_message_bytes:
            return "552 5.3.4 Message too big"

        peer = session.peer[0] if session.peer else ""
        parsed = parse_message(raw)
        spam_score = spam.score(parsed)
        folder = spam.folder_for_score(spam_score)

        delivered_any = False
        with session_scope() as db:
            # Reply / bounce detection runs once per message.
            try:
                classify_inbound(db, parsed, envelope_to=",".join(envelope.rcpt_tos))
            except Exception:  # noqa: BLE001
                log.exception("reply/bounce classification failed")

            for rcpt in envelope.rcpt_tos:
                targets = _resolve_local_targets(db, rcpt)
                forwards = list(getattr(_resolve_local_targets, "last_forwards", []))

                for mailbox in targets:
                    store = MailStore(mailbox.maildir_path)
                    key = store.add(raw, folder, seen=False)
                    db.add(Message(
                        mailbox_id=mailbox.id, maildir_key=key, folder=folder,
                        message_id=parsed.message_id, in_reply_to=parsed.in_reply_to,
                        references=parsed.references, from_addr=parsed.from_addr,
                        from_name=parsed.from_name, to_addrs=parsed.to_addrs,
                        cc_addrs=parsed.cc_addrs, subject=parsed.subject,
                        snippet=parsed.snippet, size_bytes=len(raw),
                        spam_score=spam_score, received_at=utcnow(),
                    ))
                    delivered_any = True

                for fwd in forwards:
                    spool = settings.spool_root / f"fwd_{parsed.message_id.strip('<>').split('@')[0] or key}_{fwd}.eml"
                    spool.write_bytes(raw)
                    db.add(OutboundMessage(
                        envelope_from=envelope.mail_from or f"postmaster@{settings.primary_domain}",
                        envelope_to=fwd, raw_path=str(spool), message_id=parsed.message_id,
                        subject=parsed.subject, status=OutboundStatus.QUEUED.value,
                        next_attempt_at=utcnow(),
                    ))
                    delivered_any = True

        log.info("inbound from=%s to=%s spam=%.1f peer=%s delivered=%s",
                 envelope.mail_from, envelope.rcpt_tos, spam_score, peer, delivered_any)
        return "250 Message accepted for delivery"


# ─────────────────────────────────────────────────────────────────────────────
#  Submission (587) — authenticated send
# ─────────────────────────────────────────────────────────────────────────────
def _authenticator(server, session, envelope, mechanism, auth_data):  # noqa: ANN001
    if not isinstance(auth_data, LoginPassword):
        return AuthResult(success=False, handled=False)
    login = auth_data.login.decode("utf-8", "replace")
    password = auth_data.password.decode("utf-8", "replace")
    with session_scope() as db:
        mailbox = db.scalar(select(Mailbox).where(Mailbox.address == login.lower(), Mailbox.active.is_(True)))
        if mailbox and mailbox.password_hash and verify_password(password, mailbox.password_hash):
            return AuthResult(success=True, handled=True, auth_data=login.lower())
    log.warning("submission auth failed for %s", login)
    return AuthResult(success=False, handled=False)


class SubmissionHandler:
    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: ANN001,N802
        if not session.authenticated:
            return "530 5.7.0 Authentication required"
        if len(envelope.rcpt_tos) >= _MAX_RCPT:
            return "452 4.5.3 Too many recipients"
        envelope.rcpt_tos.append(parseaddr(address)[1])
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):  # noqa: ANN001,N802
        if not session.authenticated:
            return "530 5.7.0 Authentication required"
        raw: bytes = envelope.content if isinstance(envelope.content, bytes) else envelope.content.encode()
        if len(raw) > settings.max_message_bytes:
            return "552 5.3.4 Message too big"

        auth_addr = session.auth_data if isinstance(session.auth_data, str) else envelope.mail_from
        parsed = parse_message(raw)

        with session_scope() as db:
            mailbox = db.scalar(select(Mailbox).where(Mailbox.address == (auth_addr or "").lower()))
            for rcpt in envelope.rcpt_tos:
                spool = settings.spool_root / f"sub_{(parsed.message_id or 'msg').strip('<>').split('@')[0]}_{rcpt}.eml"
                spool.write_bytes(raw)
                db.add(OutboundMessage(
                    envelope_from=auth_addr or envelope.mail_from,
                    envelope_to=rcpt, raw_path=str(spool),
                    message_id=parsed.message_id, subject=parsed.subject,
                    status=OutboundStatus.QUEUED.value, next_attempt_at=utcnow(),
                ))
            # Keep a copy in the sender's Sent folder.
            if mailbox:
                store = MailStore(mailbox.maildir_path)
                key = store.add(raw, "Sent", seen=True)
                db.add(Message(
                    mailbox_id=mailbox.id, maildir_key=key, folder="Sent",
                    message_id=parsed.message_id, in_reply_to=parsed.in_reply_to,
                    references=parsed.references, from_addr=parsed.from_addr,
                    from_name=parsed.from_name, to_addrs=parsed.to_addrs,
                    cc_addrs=parsed.cc_addrs, subject=parsed.subject,
                    snippet=parsed.snippet, size_bytes=len(raw), received_at=utcnow(),
                ))
        log.info("submission accepted from=%s to=%s", auth_addr, envelope.rcpt_tos)
        return "250 Message queued"


# ─────────────────────────────────────────────────────────────────────────────
#  Controllers
# ─────────────────────────────────────────────────────────────────────────────
def _tls_context() -> ssl.SSLContext | None:
    if not settings.tls_enabled:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(settings.tls_cert_file, settings.tls_key_file)
    return ctx


class _SubmissionController(Controller):
    def factory(self):  # noqa: D401
        tls = _tls_context()
        return SMTPProtocol(
            self.handler,
            require_starttls=False,
            tls_context=tls,
            auth_required=True,
            # Require an encrypted channel before accepting credentials whenever
            # we actually have a certificate to offer.
            auth_require_tls=bool(tls),
            authenticator=_authenticator,
            ident="Phosphor Submission",
            data_size_limit=settings.max_message_bytes,
        )


class _InboundController(Controller):
    def factory(self):  # noqa: D401
        return SMTPProtocol(
            self.handler,
            require_starttls=False,
            tls_context=_tls_context(),
            ident="Phosphor MX",
            data_size_limit=settings.max_message_bytes,
            enable_SMTPUTF8=True,
        )


_controllers: list[Controller] = []


def start_smtp_servers() -> list[Controller]:
    """Start both listeners. Returns the controllers so the caller can stop them."""
    # aiosmtpd binds all interfaces when hostname is "" and then does its
    # readiness self-check against localhost. Passing a literal "0.0.0.0" breaks
    # that self-check on Windows, so normalise the wildcard forms to "".
    bind_host = "" if settings.smtp_inbound_host in ("0.0.0.0", "::", "*") else settings.smtp_inbound_host

    inbound = _InboundController(
        InboundHandler(), hostname=bind_host, port=settings.smtp_inbound_port
    )
    submission = _SubmissionController(
        SubmissionHandler(), hostname=bind_host, port=settings.smtp_submission_port
    )
    for ctrl, label in ((inbound, "MX"), (submission, "submission")):
        try:
            ctrl.start()
            _controllers.append(ctrl)
            log.info("SMTP %s listening on %s:%s", label, ctrl.hostname, ctrl.port)
        except Exception:  # noqa: BLE001
            log.exception("could not start SMTP %s on port %s (need root for :25, or port in use)",
                          label, ctrl.port)
    return _controllers


def stop_smtp_servers() -> None:
    for ctrl in _controllers:
        try:
            ctrl.stop()
        except Exception:  # noqa: BLE001
            pass
    _controllers.clear()
