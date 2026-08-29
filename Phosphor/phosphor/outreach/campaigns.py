"""Campaign orchestration: enrolment, sequencing, message assembly, queueing."""
from __future__ import annotations

import datetime as dt
import hmac
import hashlib
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    ContactListMembership,
    ContactStatus,
    EmailEvent,
    EventType,
    Mailbox,
    OutboundMessage,
    OutboundStatus,
    RecipientStatus,
    SequenceStep,
    StepCondition,
    Template,
    utcnow,
)
from . import personalization as pz
from . import tracking
from .mime_builder import build_message, html_to_text
from .suppression import is_suppressed
from .throttle import campaign_can_send_now, jitter_delay, next_window_open


# ── unsubscribe tokens ──────────────────────────────────────────────────────
def unsubscribe_token(campaign_id: int, contact_id: int) -> str:
    msg = f"{campaign_id}:{contact_id}".encode()
    sig = hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).hexdigest()[:20]
    return f"{campaign_id}.{contact_id}.{sig}"


def verify_unsubscribe_token(token: str) -> tuple[int, int] | None:
    try:
        cid_s, kid_s, sig = token.split(".")
        campaign_id, contact_id = int(cid_s), int(kid_s)
    except (ValueError, AttributeError):
        return None
    if hmac.compare_digest(sig, unsubscribe_token(campaign_id, contact_id).split(".")[-1]):
        return campaign_id, contact_id
    return None


def unsubscribe_url(campaign_id: int, contact_id: int) -> str:
    return f"{settings.public_url.rstrip('/')}/u/{unsubscribe_token(campaign_id, contact_id)}"


# ── enrolment ───────────────────────────────────────────────────────────────
def enroll_list(db: Session, campaign: Campaign) -> int:
    """Create recipient rows for every eligible contact on the campaign's list."""
    if not campaign.list_id:
        return 0
    existing = {
        r for (r,) in db.execute(
            select(CampaignRecipient.contact_id).where(CampaignRecipient.campaign_id == campaign.id)
        )
    }
    contacts = db.scalars(
        select(Contact)
        .join(ContactListMembership, ContactListMembership.contact_id == Contact.id)
        .where(ContactListMembership.list_id == campaign.list_id)
    ).all()

    added = 0
    for contact in contacts:
        if contact.id in existing:
            continue
        if contact.status != ContactStatus.ACTIVE.value or is_suppressed(db, contact.email):
            continue
        db.add(CampaignRecipient(
            campaign_id=campaign.id,
            contact_id=contact.id,
            status=RecipientStatus.PENDING.value,
            current_step=0,
            next_action_at=campaign.start_at or utcnow(),
        ))
        added += 1
    db.flush()
    return added


# ── step resolution ─────────────────────────────────────────────────────────
def _step_copy(db: Session, step: SequenceStep) -> tuple[str, str, str]:
    subject, body_html, body_text = step.subject, step.body_html, step.body_text
    if step.template_id:
        tpl = db.get(Template, step.template_id)
        if tpl:
            subject = subject or tpl.subject
            body_html = body_html or tpl.body_html
            body_text = body_text or tpl.body_text
    if not body_text:
        body_text = html_to_text(body_html)
    return subject, body_html, body_text


def _condition_met(db: Session, campaign: Campaign, recipient: CampaignRecipient, step: SequenceStep) -> bool:
    if step.condition == StepCondition.ALWAYS.value:
        return True
    if step.condition == StepCondition.IF_NO_REPLY.value:
        return recipient.status in (RecipientStatus.ACTIVE.value, RecipientStatus.PENDING.value)
    if step.condition == StepCondition.IF_NO_OPEN.value:
        opened = db.scalar(
            select(func.count()).select_from(EmailEvent).where(
                EmailEvent.campaign_id == campaign.id,
                EmailEvent.contact_id == recipient.contact_id,
                EmailEvent.type == EventType.OPEN.value,
            )
        )
        return not opened
    return True


# ── message assembly + queueing ─────────────────────────────────────────────
def _from_identity(db: Session, campaign: Campaign) -> tuple[str, str]:
    mb = db.get(Mailbox, campaign.from_mailbox_id) if campaign.from_mailbox_id else None
    if mb:
        return mb.address, (mb.display_name or "")
    return f"noreply@{settings.primary_domain}", ""


def send_step(db: Session, campaign: Campaign, recipient: CampaignRecipient, step: SequenceStep) -> OutboundMessage:
    contact = db.get(Contact, recipient.contact_id)
    from_addr, from_name = _from_identity(db, campaign)
    subject_tpl, html_tpl, text_tpl = _step_copy(db, step)

    ctx = pz.context_for_contact(contact, {"sender_name": from_name, "sender_email": from_addr})
    seed = f"{campaign.id}:{contact.id}:{step.step_order}"
    subject, body_html, body_text = pz.render_all(
        subject=subject_tpl, body_html=html_tpl, body_text=text_tpl, ctx=ctx, seed=seed
    )

    is_followup = step.step_order > 1 and step.same_thread and recipient.thread_message_id
    in_reply_to = recipient.thread_message_id if is_followup else ""
    if is_followup and recipient.thread_subject:
        subject = recipient.thread_subject if recipient.thread_subject.lower().startswith("re:") \
            else f"Re: {recipient.thread_subject}"

    unsub = unsubscribe_url(campaign.id, contact.id)

    # Tracking + compliance footer (added to HTML; plain-text gets a line too).
    if body_html:
        if campaign.track_clicks:
            body_html = tracking.wrap_links(
                db, body_html, campaign_id=campaign.id, contact_id=contact.id,
                outbound_message_id=None, step_order=step.step_order,
            )
        footer = (
            f'<div style="margin-top:24px;font-size:12px;color:#6b7280">'
            f'<a href="{unsub}" style="color:#6b7280">Unsubscribe</a></div>'
        )
        body_html = body_html + footer
        if campaign.track_opens:
            pixel = tracking.make_open_pixel(
                db, campaign_id=campaign.id, contact_id=contact.id,
                outbound_message_id=None, step_order=step.step_order,
            )
            body_html = tracking.inject_open_pixel(body_html, pixel)
    body_text = (body_text or "").rstrip() + f"\n\n---\nUnsubscribe: {unsub}\n"

    raw, message_id = build_message(
        from_addr=from_addr, from_name=from_name, to=[contact.email],
        subject=subject, body_text=body_text, body_html=body_html or "",
        reply_to=campaign.reply_to or "", in_reply_to=in_reply_to,
        list_unsubscribe_url=unsub, is_bulk=True,
    )

    spool_path = settings.spool_root / f"cam{campaign.id}_r{recipient.id}_s{step.step_order}_{message_id.strip('<>').split('@')[0]}.eml"
    Path(spool_path).write_bytes(raw)

    ob = OutboundMessage(
        envelope_from=from_addr, envelope_to=contact.email, raw_path=str(spool_path),
        message_id=message_id, subject=subject, status=OutboundStatus.QUEUED.value,
        next_attempt_at=utcnow(), campaign_id=campaign.id, contact_id=contact.id,
        sequence_step_id=step.id,
    )
    db.add(ob)
    db.flush()

    # Back-fill outbound id on the tracking tokens we just made for this send.
    from ..models import TrackingToken
    db.query(TrackingToken).filter(
        TrackingToken.campaign_id == campaign.id,
        TrackingToken.contact_id == contact.id,
        TrackingToken.step_order == step.step_order,
        TrackingToken.outbound_message_id.is_(None),
    ).update({TrackingToken.outbound_message_id: ob.id}, synchronize_session=False)

    db.add(EmailEvent(
        type=EventType.SENT.value, campaign_id=campaign.id, contact_id=contact.id,
        outbound_message_id=ob.id, step_order=step.step_order, created_at=utcnow(),
    ))

    # Thread anchoring on the first step.
    if step.step_order == 1:
        recipient.thread_message_id = message_id
        recipient.thread_subject = subject
    recipient.current_step = step.step_order
    recipient.last_sent_at = utcnow()
    recipient.status = RecipientStatus.ACTIVE.value
    db.flush()
    return ob


# ── the per-tick driver ─────────────────────────────────────────────────────
def _next_step(db: Session, campaign: Campaign, after_order: int) -> SequenceStep | None:
    return db.scalar(
        select(SequenceStep)
        .where(SequenceStep.campaign_id == campaign.id, SequenceStep.step_order > after_order)
        .order_by(SequenceStep.step_order)
        .limit(1)
    )


def advance_recipient(db: Session, campaign: Campaign, recipient: CampaignRecipient) -> str:
    """Send the recipient's next due step (if any). Returns a short status word."""
    contact = db.get(Contact, recipient.contact_id)
    if contact is None:
        recipient.status = RecipientStatus.STOPPED_ERROR.value
        return "no-contact"
    if is_suppressed(db, contact.email) or contact.status != ContactStatus.ACTIVE.value:
        recipient.status = RecipientStatus.STOPPED_UNSUBSCRIBED.value
        recipient.next_action_at = None
        return "suppressed"

    step = _next_step(db, campaign, recipient.current_step)
    if step is None:
        recipient.status = RecipientStatus.COMPLETED.value
        recipient.next_action_at = None
        return "completed"

    if not _condition_met(db, campaign, recipient, step):
        # Skip this step, look for a later one on the next tick.
        recipient.current_step = step.step_order
        recipient.next_action_at = utcnow()
        return "skipped"

    send_step(db, campaign, recipient, step)

    following = _next_step(db, campaign, step.step_order)
    if following is None:
        recipient.status = RecipientStatus.COMPLETED.value
        recipient.next_action_at = None
    else:
        wait = dt.timedelta(days=max(0, following.wait_days))
        recipient.next_action_at = utcnow() + wait
    return "sent"


def tick(db: Session) -> dict:
    """Called on a schedule by the worker. Sends at most one message per
    campaign per tick so the jitter delay actually spaces messages out."""
    summary: dict[str, int] = {"campaigns": 0, "sent": 0, "skipped": 0, "completed": 0}
    campaigns = db.scalars(
        select(Campaign).where(Campaign.status == CampaignStatus.RUNNING.value)
    ).all()

    for campaign in campaigns:
        summary["campaigns"] += 1
        ok, _reason = campaign_can_send_now(db, campaign)
        if not ok:
            continue

        recipient = db.scalar(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status.in_(
                    [RecipientStatus.PENDING.value, RecipientStatus.ACTIVE.value]
                ),
                CampaignRecipient.next_action_at.is_not(None),
                CampaignRecipient.next_action_at <= utcnow(),
            )
            .order_by(CampaignRecipient.next_action_at)
            .limit(1)
        )
        if recipient is None:
            # Anything still pending/active but scheduled for later?
            pending = db.scalar(
                select(func.count()).select_from(CampaignRecipient).where(
                    CampaignRecipient.campaign_id == campaign.id,
                    CampaignRecipient.status.in_(
                        [RecipientStatus.PENDING.value, RecipientStatus.ACTIVE.value]
                    ),
                )
            )
            if not pending:
                campaign.status = CampaignStatus.COMPLETED.value
                summary["completed"] += 1
            continue

        outcome = advance_recipient(db, campaign, recipient)
        summary[outcome if outcome in summary else "skipped"] = summary.get(outcome, 0) + 1
        if outcome == "sent":
            summary["sent"] += 1
            # Nudge the *next* due recipient out by a jittered delay.
            nxt = db.scalar(
                select(CampaignRecipient).where(
                    CampaignRecipient.campaign_id == campaign.id,
                    CampaignRecipient.status.in_(
                        [RecipientStatus.PENDING.value, RecipientStatus.ACTIVE.value]
                    ),
                    CampaignRecipient.next_action_at.is_not(None),
                    CampaignRecipient.next_action_at <= utcnow(),
                ).order_by(CampaignRecipient.next_action_at).limit(1)
            )
            if nxt is not None:
                nxt.next_action_at = utcnow() + jitter_delay(campaign)

    db.commit()
    return summary


def reschedule_paused_campaign(db: Session, campaign: Campaign) -> None:
    """When a campaign resumes outside its window, push due recipients to the
    next window open so we don't fire a burst at a bad hour."""
    open_at = next_window_open(campaign)
    db.query(CampaignRecipient).filter(
        CampaignRecipient.campaign_id == campaign.id,
        CampaignRecipient.status.in_([RecipientStatus.PENDING.value, RecipientStatus.ACTIVE.value]),
        CampaignRecipient.next_action_at < open_at,
    ).update({CampaignRecipient.next_action_at: open_at}, synchronize_session=False)
    db.commit()
