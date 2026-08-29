"""Sending-window, daily-cap and global-rate calculations for campaigns."""
from __future__ import annotations

import datetime as dt
import random
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Campaign, EmailEvent, EventType, OutboundMessage, OutboundStatus


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def in_sending_window(campaign: Campaign, now_utc: dt.datetime | None = None) -> bool:
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    local = now_utc.astimezone(_tz(campaign.timezone))
    if local.weekday() not in (campaign.send_days or [0, 1, 2, 3, 4]):
        return False
    start = campaign.window_start_hour
    end = campaign.window_end_hour
    if start == end:
        return True
    if start < end:
        return start <= local.hour < end
    # window wraps past midnight
    return local.hour >= start or local.hour < end


def next_window_open(campaign: Campaign, now_utc: dt.datetime | None = None) -> dt.datetime:
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    tz = _tz(campaign.timezone)
    local = now_utc.astimezone(tz)
    for day_offset in range(0, 8):
        day = (local + dt.timedelta(days=day_offset)).date()
        weekday = (local + dt.timedelta(days=day_offset)).weekday()
        if weekday not in (campaign.send_days or [0, 1, 2, 3, 4]):
            continue
        candidate = dt.datetime.combine(day, dt.time(hour=campaign.window_start_hour), tzinfo=tz)
        if candidate.astimezone(dt.timezone.utc) > now_utc:
            return candidate.astimezone(dt.timezone.utc)
        if day_offset == 0 and in_sending_window(campaign, now_utc):
            return now_utc
    return now_utc + dt.timedelta(hours=1)


def sent_today(db: Session, campaign_id: int, campaign: Campaign) -> int:
    tz = _tz(campaign.timezone)
    local_midnight = dt.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    since = local_midnight.astimezone(dt.timezone.utc)
    return int(db.scalar(
        select(func.count()).select_from(EmailEvent).where(
            EmailEvent.campaign_id == campaign_id,
            EmailEvent.type == EventType.SENT.value,
            EmailEvent.created_at >= since,
        )
    ) or 0)


def global_sent_last_hour(db: Session) -> int:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    return int(db.scalar(
        select(func.count()).select_from(OutboundMessage).where(
            OutboundMessage.status == OutboundStatus.SENT.value,
            OutboundMessage.sent_at >= since,
        )
    ) or 0)


def campaign_can_send_now(db: Session, campaign: Campaign) -> tuple[bool, str]:
    if not settings.outbound_enabled:
        return False, "outbound sending is disabled in config"
    if not in_sending_window(campaign):
        return False, "outside sending window"
    if sent_today(db, campaign.id, campaign) >= campaign.daily_cap:
        return False, "daily cap reached"
    if global_sent_last_hour(db) >= settings.max_send_per_hour:
        return False, "global hourly cap reached"
    return True, "ok"


def jitter_delay(campaign: Campaign) -> dt.timedelta:
    lo = max(0, campaign.min_delay_seconds)
    hi = max(lo, campaign.max_delay_seconds)
    return dt.timedelta(seconds=random.randint(lo, hi))
