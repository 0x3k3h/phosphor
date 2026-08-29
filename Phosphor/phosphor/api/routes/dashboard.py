"""Control-panel dashboard numbers."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...auth import current_user
from ...database import get_db
from ...models import (
    Campaign,
    CampaignStatus,
    Contact,
    Domain,
    EmailEvent,
    EventType,
    Mailbox,
    Message,
    OutboundMessage,
    OutboundStatus,
    Suppression,
)
from ...schemas import DashboardStats

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(current_user)])


def _count(db: Session, model) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


@router.get("/stats", response_model=DashboardStats)
def dashboard_stats(db: Session = Depends(get_db)) -> DashboardStats:
    now = dt.datetime.now(dt.timezone.utc)
    day_ago = now - dt.timedelta(hours=24)
    week_ago = now - dt.timedelta(days=7)

    sent_24h = db.scalar(
        select(func.count()).select_from(OutboundMessage).where(
            OutboundMessage.status == OutboundStatus.SENT.value,
            OutboundMessage.sent_at >= day_ago,
        )
    )
    failed_24h = db.scalar(
        select(func.count()).select_from(OutboundMessage).where(
            OutboundMessage.status == OutboundStatus.FAILED.value,
            OutboundMessage.created_at >= day_ago,
        )
    )
    queued = db.scalar(
        select(func.count()).select_from(OutboundMessage).where(
            OutboundMessage.status.in_([OutboundStatus.QUEUED.value, OutboundStatus.DEFERRED.value])
        )
    )
    inbound_24h = db.scalar(
        select(func.count()).select_from(Message).where(Message.received_at >= day_ago, Message.folder != "Sent")
    )
    replies_7d = db.scalar(
        select(func.count()).select_from(EmailEvent).where(
            EmailEvent.type == EventType.REPLY.value, EmailEvent.created_at >= week_ago
        )
    )
    running = db.scalar(
        select(func.count()).select_from(Campaign).where(Campaign.status == CampaignStatus.RUNNING.value)
    )

    return DashboardStats(
        domains=_count(db, Domain),
        mailboxes=_count(db, Mailbox),
        contacts=_count(db, Contact),
        campaigns_running=int(running or 0),
        outbound_queued=int(queued or 0),
        outbound_sent_24h=int(sent_24h or 0),
        outbound_failed_24h=int(failed_24h or 0),
        inbound_24h=int(inbound_24h or 0),
        replies_7d=int(replies_7d or 0),
        suppressed=_count(db, Suppression),
    )


@router.get("/activity")
def recent_activity(limit: int = 40, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(EmailEvent).order_by(EmailEvent.created_at.desc()).limit(limit)
    ).all()
    return [
        {"type": e.type, "campaign_id": e.campaign_id, "contact_id": e.contact_id,
         "step": e.step_order, "url": e.url, "at": e.created_at}
        for e in rows
    ]
