"""Do-not-contact enforcement."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Contact, ContactStatus, EmailEvent, EventType, Suppression, utcnow


def is_suppressed(db: Session, email: str) -> bool:
    email = (email or "").strip().lower()
    if not email:
        return True
    return db.scalar(select(Suppression.id).where(Suppression.email == email)) is not None


def suppress(
    db: Session,
    email: str,
    *,
    reason: str = "manual",
    campaign_id: int | None = None,
    note: str = "",
) -> Suppression:
    email = (email or "").strip().lower()
    existing = db.scalar(select(Suppression).where(Suppression.email == email))
    if existing:
        return existing
    row = Suppression(email=email, reason=reason, campaign_id=campaign_id, note=note)
    db.add(row)

    contact = db.scalar(select(Contact).where(Contact.email == email))
    if contact:
        if reason == "unsubscribe":
            contact.status = ContactStatus.UNSUBSCRIBED.value
        elif reason == "bounce":
            contact.status = ContactStatus.BOUNCED.value
        elif reason == "complaint":
            contact.status = ContactStatus.COMPLAINED.value
        db.add(EmailEvent(
            type=EventType.UNSUBSCRIBE.value if reason == "unsubscribe" else
                 (EventType.BOUNCE.value if reason == "bounce" else EventType.COMPLAINT.value),
            campaign_id=campaign_id, contact_id=contact.id, created_at=utcnow(),
        ))
    db.flush()
    return row


def unsuppress(db: Session, email: str) -> None:
    email = (email or "").strip().lower()
    row = db.scalar(select(Suppression).where(Suppression.email == email))
    if row:
        db.delete(row)
        db.flush()
