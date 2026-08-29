"""Background scheduler: drives the outbound queue and campaign sequencing."""
from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from .database import session_scope
from .dns.records import check_domain
from .models import Domain, utcnow
from .outreach import campaigns as camp
from .smtp import sender

log = logging.getLogger("phosphor.worker")
_scheduler: BackgroundScheduler | None = None


def _job_outbound() -> None:
    try:
        with session_scope() as db:
            result = sender.process_queue(db, limit=25)
        if result["attempted"]:
            log.info("outbound: %s", result)
    except Exception:  # noqa: BLE001
        log.exception("outbound job failed")


def _job_campaigns() -> None:
    try:
        with session_scope() as db:
            result = camp.tick(db)
        if result.get("sent"):
            log.info("campaigns: %s", result)
    except Exception:  # noqa: BLE001
        log.exception("campaign job failed")


def _job_dns_refresh() -> None:
    try:
        with session_scope() as db:
            for domain in db.scalars(select(Domain).where(Domain.active.is_(True))).all():
                status = check_domain(domain)
                domain.dns_status = {k: v.get("ok") for k, v in status.items() if not k.startswith("_")}
                domain.dns_last_checked_at = utcnow()
    except Exception:  # noqa: BLE001
        log.exception("dns refresh job failed")


def start_worker() -> BackgroundScheduler:
    global _scheduler
    if _scheduler:
        return _scheduler
    sched = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1})
    soon = utcnow() + dt.timedelta(minutes=5)
    sched.add_job(_job_outbound, "interval", seconds=20, id="outbound", next_run_time=utcnow())
    sched.add_job(_job_campaigns, "interval", seconds=30, id="campaigns", next_run_time=utcnow())
    # DNS checks hit the network for every record of every domain; don't do that
    # in the first seconds of boot.
    sched.add_job(_job_dns_refresh, "interval", hours=6, id="dns", next_run_time=soon)
    sched.start()
    _scheduler = sched
    log.info("worker started (outbound=20s, campaigns=30s, dns=6h)")
    return sched


def stop_worker() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
