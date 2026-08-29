"""Inbound classification: is this a human reply to a campaign, or a bounce?

Called from the inbound SMTP handler for every accepted message.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..mailstore.parser import ParsedMessage
from ..models import (
    Campaign,
    CampaignRecipient,
    Contact,
    EmailEvent,
    EventType,
    OutboundMessage,
    RecipientStatus,
    SequenceStep,
    utcnow,
)
from ..outreach.suppression import suppress

_BOUNCE_SENDERS = re.compile(r"(mailer-daemon|postmaster|no-?reply)@", re.IGNORECASE)
_HARD_BOUNCE_CODES = re.compile(r"\b5\.\d\.\d\b|\b55[0-9]\b")
_MSGID_RE = re.compile(r"<[^>]+>")


def _referenced_ids(parsed: ParsedMessage) -> list[str]:
    ids = []
    if parsed.in_reply_to:
        ids += _MSGID_RE.findall(parsed.in_reply_to)
    if parsed.references:
        ids += _MSGID_RE.findall(parsed.references)
    return ids


def _match_outbound(db: Session, parsed: ParsedMessage) -> OutboundMessage | None:
    ids = _referenced_ids(parsed)
    for mid in ids:
        row = db.scalar(select(OutboundMessage).where(OutboundMessage.message_id == mid))
        if row:
            return row
    # Fall back to matching by sender address against a campaign recipient.
    if parsed.from_addr:
        row = db.scalar(
            select(OutboundMessage)
            .join(Contact, Contact.id == OutboundMessage.contact_id)
            .where(Contact.email == parsed.from_addr, OutboundMessage.campaign_id.is_not(None))
            .order_by(OutboundMessage.created_at.desc())
        )
        return row
    return None


def classify_inbound(db: Session, parsed: ParsedMessage, *, envelope_to: str) -> str:
    """Returns one of: 'reply', 'bounce', 'normal'. Records the matching event."""
    is_bounceish = (
        _BOUNCE_SENDERS.search(parsed.from_addr or "")
        or "mail delivery" in (parsed.subject or "").lower()
        or "undeliverable" in (parsed.subject or "").lower()
        or "delivery status notification" in (parsed.subject or "").lower()
    )

    ob = _match_outbound(db, parsed)

    if is_bounceish:
        body = f"{parsed.subject}\n{parsed.body_text}\n{parsed.body_html}"
        hard = bool(_HARD_BOUNCE_CODES.search(body))
        target_email = None
        if ob and ob.contact_id:
            contact = db.get(Contact, ob.contact_id)
            target_email = contact.email if contact else None
        if not target_email:
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", parsed.body_text or "")
            target_email = m.group(0).lower() if m else None

        if target_email and hard:
            suppress(db, target_email, reason="bounce",
                     campaign_id=ob.campaign_id if ob else None, note=(parsed.subject or "")[:200])
            if ob and ob.campaign_id and ob.contact_id:
                rcpt = db.scalar(select(CampaignRecipient).where(
                    CampaignRecipient.campaign_id == ob.campaign_id,
                    CampaignRecipient.contact_id == ob.contact_id,
                ))
                if rcpt:
                    rcpt.status = RecipientStatus.STOPPED_BOUNCED.value
                    rcpt.next_action_at = None
        if ob:
            db.add(EmailEvent(
                type=EventType.BOUNCE.value, campaign_id=ob.campaign_id, contact_id=ob.contact_id,
                outbound_message_id=ob.id, meta={"hard": hard}, created_at=utcnow(),
            ))
        db.flush()
        return "bounce"

    if ob and ob.campaign_id:
        campaign = db.get(Campaign, ob.campaign_id)
        rcpt = db.scalar(select(CampaignRecipient).where(
            CampaignRecipient.campaign_id == ob.campaign_id,
            CampaignRecipient.contact_id == ob.contact_id,
        ))
        step_order = None
        if ob.sequence_step_id:
            step = db.get(SequenceStep, ob.sequence_step_id)
            step_order = step.step_order if step else None
        db.add(EmailEvent(
            type=EventType.REPLY.value, campaign_id=ob.campaign_id, contact_id=ob.contact_id,
            outbound_message_id=ob.id, step_order=step_order, created_at=utcnow(),
            meta={"subject": parsed.subject[:200]},
        ))
        if rcpt and campaign and campaign.stop_on_reply:
            rcpt.status = RecipientStatus.STOPPED_REPLIED.value
            rcpt.next_action_at = None
        if ob.contact_id:
            contact = db.get(Contact, ob.contact_id)
            if contact and contact.status == "active":
                contact.status = "replied"
        db.flush()
        return "reply"

    return "normal"
