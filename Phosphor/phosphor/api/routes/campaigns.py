"""Cold-email campaigns: build a sequence, enrol a list, run it, watch the numbers."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...auth import current_user
from ...config import settings
from ...database import get_db
from ...models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    ContactList,
    EmailEvent,
    Mailbox,
    OutboundMessage,
    OutboundStatus,
    SequenceStep,
    Template,
    utcnow,
)
from ...outreach import personalization as pz
from ...outreach.campaigns import enroll_list, reschedule_paused_campaign
from ...outreach.mime_builder import build_message, html_to_text
from ...outreach.stats import campaign_stats
from ...outreach.throttle import in_sending_window, next_window_open
from ...schemas import (
    CampaignCreate,
    CampaignOut,
    CampaignStats,
    CampaignUpdate,
    SequenceStepIn,
    SequenceStepOut,
)

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"], dependencies=[Depends(current_user)])


def _get(db: Session, campaign_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "campaign not found")
    return campaign


@router.get("", response_model=list[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)) -> list[Campaign]:
    return db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()


@router.post("", response_model=CampaignOut, status_code=201)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)) -> Campaign:
    if payload.from_mailbox_id and not db.get(Mailbox, payload.from_mailbox_id):
        raise HTTPException(422, "from_mailbox_id not found")
    if payload.list_id and not db.get(ContactList, payload.list_id):
        raise HTTPException(422, "list_id not found")
    campaign = Campaign(**payload.model_dump())
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: int, db: Session = Depends(get_db)) -> Campaign:
    return _get(db, campaign_id)


@router.put("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: int, payload: CampaignUpdate, db: Session = Depends(get_db)) -> Campaign:
    campaign = _get(db, campaign_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, key, value)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    campaign = _get(db, campaign_id)
    db.delete(campaign)
    db.commit()


# ── Sequence steps ──────────────────────────────────────────────────────────
@router.put("/{campaign_id}/steps", response_model=list[SequenceStepOut])
def set_steps(campaign_id: int, steps: list[SequenceStepIn], db: Session = Depends(get_db)) -> list[SequenceStep]:
    campaign = _get(db, campaign_id)
    if campaign.status in (CampaignStatus.RUNNING.value,):
        raise HTTPException(409, "pause the campaign before editing its sequence")

    db.query(SequenceStep).filter(SequenceStep.campaign_id == campaign_id).delete()
    db.flush()

    made: list[SequenceStep] = []
    for i, step in enumerate(sorted(steps, key=lambda s: s.step_order), start=1):
        if step.template_id and not db.get(Template, step.template_id):
            raise HTTPException(422, f"step {i}: template_id {step.template_id} not found")
        row = SequenceStep(
            campaign_id=campaign_id, step_order=i, template_id=step.template_id,
            subject=step.subject, body_html=step.body_html, body_text=step.body_text,
            wait_days=max(0, step.wait_days), condition=step.condition, same_thread=step.same_thread,
        )
        db.add(row)
        made.append(row)
    db.commit()
    for row in made:
        db.refresh(row)
    return made


@router.get("/{campaign_id}/steps", response_model=list[SequenceStepOut])
def get_steps(campaign_id: int, db: Session = Depends(get_db)) -> list[SequenceStep]:
    _get(db, campaign_id)
    return db.scalars(
        select(SequenceStep).where(SequenceStep.campaign_id == campaign_id).order_by(SequenceStep.step_order)
    ).all()


# ── Lifecycle ───────────────────────────────────────────────────────────────
@router.post("/{campaign_id}/enroll")
def enroll(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    campaign = _get(db, campaign_id)
    if not campaign.list_id:
        raise HTTPException(422, "assign a contact list first")
    added = enroll_list(db, campaign)
    db.commit()
    total = db.scalar(
        select(func.count()).select_from(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
    )
    return {"added": added, "recipients_total": int(total or 0)}


@router.post("/{campaign_id}/start", response_model=CampaignOut)
def start_campaign(campaign_id: int, db: Session = Depends(get_db)) -> Campaign:
    campaign = _get(db, campaign_id)
    steps = db.scalar(select(func.count()).select_from(SequenceStep).where(SequenceStep.campaign_id == campaign_id))
    if not steps:
        raise HTTPException(422, "add at least one sequence step")
    if not campaign.from_mailbox_id:
        raise HTTPException(422, "choose a sending mailbox")

    if not db.scalar(select(func.count()).select_from(CampaignRecipient).where(
        CampaignRecipient.campaign_id == campaign_id
    )):
        enroll_list(db, campaign)

    campaign.status = CampaignStatus.RUNNING.value
    if not campaign.start_at:
        campaign.start_at = utcnow()
    db.commit()
    reschedule_paused_campaign(db, campaign)
    db.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
def pause_campaign(campaign_id: int, db: Session = Depends(get_db)) -> Campaign:
    campaign = _get(db, campaign_id)
    campaign.status = CampaignStatus.PAUSED.value
    db.commit()
    db.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/resume", response_model=CampaignOut)
def resume_campaign(campaign_id: int, db: Session = Depends(get_db)) -> Campaign:
    campaign = _get(db, campaign_id)
    campaign.status = CampaignStatus.RUNNING.value
    db.commit()
    reschedule_paused_campaign(db, campaign)
    db.refresh(campaign)
    return campaign


# ── Stats & recipients ──────────────────────────────────────────────────────
@router.get("/{campaign_id}/stats", response_model=CampaignStats)
def stats(campaign_id: int, db: Session = Depends(get_db)) -> CampaignStats:
    return campaign_stats(db, _get(db, campaign_id))


@router.get("/{campaign_id}/recipients")
def recipients(
    campaign_id: int, status: str | None = None, limit: int = 100, offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict]:
    _get(db, campaign_id)
    stmt = (
        select(CampaignRecipient, Contact)
        .join(Contact, Contact.id == CampaignRecipient.contact_id)
        .where(CampaignRecipient.campaign_id == campaign_id)
        .order_by(CampaignRecipient.id)
        .limit(limit).offset(offset)
    )
    if status:
        stmt = stmt.where(CampaignRecipient.status == status)
    out = []
    for rcpt, contact in db.execute(stmt).all():
        out.append({
            "id": rcpt.id, "contact_id": contact.id, "email": contact.email,
            "name": f"{contact.first_name} {contact.last_name}".strip(),
            "company": contact.company, "status": rcpt.status,
            "current_step": rcpt.current_step,
            "next_action_at": rcpt.next_action_at, "last_sent_at": rcpt.last_sent_at,
        })
    return out


@router.get("/{campaign_id}/timeline")
def timeline(campaign_id: int, limit: int = 100, db: Session = Depends(get_db)) -> list[dict]:
    _get(db, campaign_id)
    rows = db.execute(
        select(EmailEvent, Contact.email)
        .outerjoin(Contact, Contact.id == EmailEvent.contact_id)
        .where(EmailEvent.campaign_id == campaign_id)
        .order_by(EmailEvent.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "type": ev.type, "email": email, "step": ev.step_order,
            "url": ev.url, "at": ev.created_at, "meta": ev.meta,
        }
        for ev, email in rows
    ]


# ── Test send ───────────────────────────────────────────────────────────────
class TestSendRequest(BaseModel):
    to: EmailStr
    step_order: int = 1
    contact_id: int | None = None


@router.post("/{campaign_id}/test-send")
def test_send(campaign_id: int, payload: TestSendRequest, db: Session = Depends(get_db)) -> dict:
    campaign = _get(db, campaign_id)
    step = db.scalar(
        select(SequenceStep).where(
            SequenceStep.campaign_id == campaign_id, SequenceStep.step_order == payload.step_order
        )
    )
    if not step:
        raise HTTPException(404, f"step {payload.step_order} not found")

    mailbox = db.get(Mailbox, campaign.from_mailbox_id) if campaign.from_mailbox_id else None
    if not mailbox:
        raise HTTPException(422, "campaign has no sending mailbox")

    subject, body_html, body_text = step.subject, step.body_html, step.body_text
    if step.template_id:
        tpl = db.get(Template, step.template_id)
        if tpl:
            subject = subject or tpl.subject
            body_html = body_html or tpl.body_html
            body_text = body_text or tpl.body_text
    body_text = body_text or html_to_text(body_html)

    if payload.contact_id:
        contact = db.get(Contact, payload.contact_id)
        ctx = pz.context_for_contact(contact) if contact else {}
    else:
        sample = Contact(email=str(payload.to), first_name="Jordan", last_name="Lee",
                         company="Acme", title="Head of Growth")
        ctx = pz.context_for_contact(sample)

    subject, body_html, body_text = pz.render_all(
        subject=subject, body_html=body_html, body_text=body_text, ctx=ctx, seed="test",
    )
    subject = f"[TEST] {subject}"

    raw, message_id = build_message(
        from_addr=mailbox.address, from_name=mailbox.display_name, to=[str(payload.to)],
        subject=subject, body_text=body_text, body_html=body_html or "",
        reply_to=campaign.reply_to or "",
    )
    spool = settings.spool_root / f"test_{campaign_id}_{message_id.strip('<>').split('@')[0]}.eml"
    Path(spool).write_bytes(raw)
    db.add(OutboundMessage(
        envelope_from=mailbox.address, envelope_to=str(payload.to), raw_path=str(spool),
        message_id=message_id, subject=subject, status=OutboundStatus.QUEUED.value,
        next_attempt_at=utcnow(),
    ))
    db.commit()
    return {"ok": True, "queued_to": str(payload.to), "subject": subject}


@router.get("/{campaign_id}/window")
def window_info(campaign_id: int, db: Session = Depends(get_db)) -> dict:
    campaign = _get(db, campaign_id)
    return {
        "in_window_now": in_sending_window(campaign),
        "next_window_open": next_window_open(campaign),
        "timezone": campaign.timezone,
    }
