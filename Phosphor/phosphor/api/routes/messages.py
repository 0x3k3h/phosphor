"""Webmail: browse mailboxes, read messages, compose and send."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ...auth import current_user
from ...config import settings
from ...database import get_db
from ...mailstore import MailStore, parse_message
from ...models import Folder, Mailbox, Message, OutboundMessage, OutboundStatus, utcnow
from ...outreach.mime_builder import build_message
from ...schemas import ComposeRequest, MessageDetail, MessageOut

router = APIRouter(prefix="/api/mail", tags=["webmail"], dependencies=[Depends(current_user)])


def _get_mailbox(db: Session, mailbox_id: int) -> Mailbox:
    mailbox = db.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(404, "mailbox not found")
    return mailbox


@router.get("/{mailbox_id}/folders")
def folders(mailbox_id: int, db: Session = Depends(get_db)) -> dict:
    _get_mailbox(db, mailbox_id)
    unread_expr = func.sum(case((Message.is_read.is_(False), 1), else_=0))
    rows = db.execute(
        select(Message.folder, func.count(), unread_expr)
        .where(Message.mailbox_id == mailbox_id)
        .group_by(Message.folder)
    ).all()
    counts = {f.value: {"total": 0, "unread": 0} for f in Folder}
    for folder, total, unread in rows:
        counts.setdefault(folder, {"total": 0, "unread": 0})
        counts[folder] = {"total": int(total or 0), "unread": int(unread or 0)}
    return counts


@router.get("/{mailbox_id}/messages", response_model=list[MessageOut])
def list_messages(
    mailbox_id: int,
    folder: str = "INBOX",
    q: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[Message]:
    _get_mailbox(db, mailbox_id)
    stmt = (
        select(Message)
        .where(Message.mailbox_id == mailbox_id, Message.folder == folder)
        .order_by(Message.received_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Message.subject.ilike(like) | Message.from_addr.ilike(like) | Message.snippet.ilike(like)
        )
    return db.scalars(stmt).all()


@router.get("/{mailbox_id}/messages/{message_id}", response_model=MessageDetail)
def get_message(mailbox_id: int, message_id: int, db: Session = Depends(get_db)) -> MessageDetail:
    mailbox = _get_mailbox(db, mailbox_id)
    msg = db.get(Message, message_id)
    if not msg or msg.mailbox_id != mailbox_id:
        raise HTTPException(404, "message not found")

    store = MailStore(mailbox.maildir_path)
    raw = store.get_bytes(msg.folder, msg.maildir_key)
    parsed = parse_message(raw) if raw else None

    if not msg.is_read:
        msg.is_read = True
        store.set_flags(msg.folder, msg.maildir_key, seen=True)
        db.commit()

    detail = MessageDetail.model_validate(msg)
    if parsed:
        detail.body_html = parsed.sanitized_html
        detail.body_text = parsed.body_text
        detail.headers = parsed.headers
        detail.attachments = [a.__dict__ for a in parsed.attachments]
    return detail


@router.get("/{mailbox_id}/messages/{message_id}/raw")
def get_raw(mailbox_id: int, message_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import PlainTextResponse

    mailbox = _get_mailbox(db, mailbox_id)
    msg = db.get(Message, message_id)
    if not msg or msg.mailbox_id != mailbox_id:
        raise HTTPException(404, "message not found")
    raw = MailStore(mailbox.maildir_path).get_bytes(msg.folder, msg.maildir_key)
    return PlainTextResponse(raw or b"", media_type="message/rfc822")


@router.post("/{mailbox_id}/messages/{message_id}/flags")
def set_flags(
    mailbox_id: int, message_id: int, is_read: bool | None = None, is_flagged: bool | None = None,
    db: Session = Depends(get_db),
) -> dict:
    mailbox = _get_mailbox(db, mailbox_id)
    msg = db.get(Message, message_id)
    if not msg or msg.mailbox_id != mailbox_id:
        raise HTTPException(404, "message not found")
    store = MailStore(mailbox.maildir_path)
    if is_read is not None:
        msg.is_read = is_read
    if is_flagged is not None:
        msg.is_flagged = is_flagged
    store.set_flags(msg.folder, msg.maildir_key, seen=msg.is_read, flagged=msg.is_flagged)
    db.commit()
    return {"ok": True}


@router.post("/{mailbox_id}/messages/{message_id}/move")
def move_message(mailbox_id: int, message_id: int, folder: str, db: Session = Depends(get_db)) -> dict:
    mailbox = _get_mailbox(db, mailbox_id)
    msg = db.get(Message, message_id)
    if not msg or msg.mailbox_id != mailbox_id:
        raise HTTPException(404, "message not found")
    valid = {f.value for f in Folder}
    if folder not in valid:
        raise HTTPException(422, f"folder must be one of {sorted(valid)}")
    store = MailStore(mailbox.maildir_path)
    new_key = store.move(msg.folder, msg.maildir_key, folder)
    if new_key:
        msg.maildir_key = new_key
    msg.folder = folder
    db.commit()
    return {"ok": True, "folder": folder}


@router.delete("/{mailbox_id}/messages/{message_id}", status_code=204)
def delete_message(mailbox_id: int, message_id: int, db: Session = Depends(get_db)):
    mailbox = _get_mailbox(db, mailbox_id)
    msg = db.get(Message, message_id)
    if not msg or msg.mailbox_id != mailbox_id:
        raise HTTPException(404, "message not found")
    store = MailStore(mailbox.maildir_path)
    if msg.folder == Folder.TRASH.value:
        store.delete(msg.folder, msg.maildir_key)
        db.delete(msg)
    else:
        new_key = store.move(msg.folder, msg.maildir_key, Folder.TRASH.value)
        if new_key:
            msg.maildir_key = new_key
        msg.folder = Folder.TRASH.value
    db.commit()


@router.post("/{mailbox_id}/send")
def compose_send(mailbox_id: int, payload: ComposeRequest, db: Session = Depends(get_db)) -> dict:
    mailbox = _get_mailbox(db, mailbox_id)
    if payload.from_mailbox_id != mailbox_id:
        raise HTTPException(422, "from_mailbox_id mismatch")

    recipients = [str(a) for a in payload.to] + [str(a) for a in payload.cc] + [str(a) for a in payload.bcc]
    if not recipients:
        raise HTTPException(422, "no recipients")

    raw, message_id = build_message(
        from_addr=mailbox.address, from_name=mailbox.display_name,
        to=[str(a) for a in payload.to], cc=[str(a) for a in payload.cc],
        subject=payload.subject, body_text=payload.body_text, body_html=payload.body_html,
        in_reply_to=payload.in_reply_to, references=payload.references,
    )

    for rcpt in recipients:
        spool = settings.spool_root / f"web_{message_id.strip('<>').split('@')[0]}_{rcpt}.eml"
        Path(spool).write_bytes(raw)
        db.add(OutboundMessage(
            envelope_from=mailbox.address, envelope_to=rcpt, raw_path=str(spool),
            message_id=message_id, subject=payload.subject,
            status=OutboundStatus.QUEUED.value, next_attempt_at=utcnow(),
        ))

    store = MailStore(mailbox.maildir_path)
    key = store.add(raw, Folder.SENT.value, seen=True)
    parsed = parse_message(raw)
    db.add(Message(
        mailbox_id=mailbox.id, maildir_key=key, folder=Folder.SENT.value,
        message_id=message_id, in_reply_to=payload.in_reply_to, references=payload.references,
        from_addr=mailbox.address, from_name=mailbox.display_name,
        to_addrs=[str(a) for a in payload.to], cc_addrs=[str(a) for a in payload.cc],
        subject=payload.subject, snippet=parsed.snippet, size_bytes=len(raw), is_read=True,
        received_at=utcnow(),
    ))
    db.commit()
    return {"ok": True, "message_id": message_id, "queued": len(recipients)}
