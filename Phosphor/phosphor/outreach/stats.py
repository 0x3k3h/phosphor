"""Aggregate campaign analytics from the email_events table."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Campaign, CampaignRecipient, EmailEvent, EventType, RecipientStatus
from ..schemas import CampaignStats


def _count(db: Session, campaign_id: int, event_type: str, *, distinct_contacts: bool = False) -> int:
    col = func.count(func.distinct(EmailEvent.contact_id)) if distinct_contacts else func.count()
    return int(db.scalar(
        select(col).select_from(EmailEvent).where(
            EmailEvent.campaign_id == campaign_id,
            EmailEvent.type == event_type,
        )
    ) or 0)


def _recipients_by_status(db: Session, campaign_id: int) -> dict[str, int]:
    rows = db.execute(
        select(CampaignRecipient.status, func.count())
        .where(CampaignRecipient.campaign_id == campaign_id)
        .group_by(CampaignRecipient.status)
    ).all()
    return {status: n for status, n in rows}


def campaign_stats(db: Session, campaign: Campaign) -> CampaignStats:
    cid = campaign.id
    by_status = _recipients_by_status(db, cid)
    total = sum(by_status.values())
    active = by_status.get(RecipientStatus.ACTIVE.value, 0) + by_status.get(RecipientStatus.PENDING.value, 0)
    completed = by_status.get(RecipientStatus.COMPLETED.value, 0)

    sent = _count(db, cid, EventType.SENT.value)
    delivered = _count(db, cid, EventType.DELIVERED.value) or sent
    opens = _count(db, cid, EventType.OPEN.value)
    unique_opens = _count(db, cid, EventType.OPEN.value, distinct_contacts=True)
    clicks = _count(db, cid, EventType.CLICK.value)
    unique_clicks = _count(db, cid, EventType.CLICK.value, distinct_contacts=True)
    replies = _count(db, cid, EventType.REPLY.value, distinct_contacts=True)
    bounces = _count(db, cid, EventType.BOUNCE.value, distinct_contacts=True)
    unsubs = _count(db, cid, EventType.UNSUBSCRIBE.value, distinct_contacts=True)

    denom = max(sent, 1)
    return CampaignStats(
        campaign_id=cid,
        recipients_total=total,
        recipients_active=active,
        recipients_completed=completed,
        sent=sent,
        delivered=delivered,
        opens=opens,
        unique_opens=unique_opens,
        clicks=clicks,
        unique_clicks=unique_clicks,
        replies=replies,
        bounces=bounces,
        unsubscribes=unsubs,
        open_rate=round(100 * unique_opens / denom, 1),
        click_rate=round(100 * unique_clicks / denom, 1),
        reply_rate=round(100 * replies / denom, 1),
        bounce_rate=round(100 * bounces / denom, 1),
    )
