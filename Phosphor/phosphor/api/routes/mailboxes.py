"""Mailboxes and forwarding aliases."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...auth import current_user
from ...crypto import hash_password
from ...database import get_db
from ...models import Alias, Domain, Mailbox
from ...schemas import AliasCreate, AliasOut, MailboxCreate, MailboxOut
from ..service import create_mailbox

router = APIRouter(prefix="/api", tags=["mailboxes"], dependencies=[Depends(current_user)])


@router.get("/mailboxes", response_model=list[MailboxOut])
def list_mailboxes(domain_id: int | None = None, db: Session = Depends(get_db)) -> list[Mailbox]:
    stmt = select(Mailbox).order_by(Mailbox.address)
    if domain_id:
        stmt = stmt.where(Mailbox.domain_id == domain_id)
    return db.scalars(stmt).all()


@router.post("/mailboxes", response_model=MailboxOut, status_code=201)
def add_mailbox(payload: MailboxCreate, db: Session = Depends(get_db)) -> Mailbox:
    mailbox = create_mailbox(
        db, domain_id=payload.domain_id, local_part=payload.local_part,
        password=payload.password, display_name=payload.display_name, quota_mb=payload.quota_mb,
    )
    db.commit()
    db.refresh(mailbox)
    return mailbox


@router.put("/mailboxes/{mailbox_id}/password", status_code=204)
def set_password(mailbox_id: int, password: str, db: Session = Depends(get_db)):
    mailbox = db.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(404, "mailbox not found")
    if len(password) < 8:
        raise HTTPException(422, "password too short")
    mailbox.password_hash = hash_password(password)
    db.commit()


@router.put("/mailboxes/{mailbox_id}", response_model=MailboxOut)
def update_mailbox(
    mailbox_id: int, display_name: str | None = None, active: bool | None = None,
    quota_mb: int | None = None, db: Session = Depends(get_db),
) -> Mailbox:
    mailbox = db.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(404, "mailbox not found")
    if display_name is not None:
        mailbox.display_name = display_name
    if active is not None:
        mailbox.active = active
    if quota_mb is not None:
        mailbox.quota_bytes = quota_mb * 1024 * 1024
    db.commit()
    db.refresh(mailbox)
    return mailbox


@router.delete("/mailboxes/{mailbox_id}", status_code=204)
def delete_mailbox(mailbox_id: int, db: Session = Depends(get_db)):
    mailbox = db.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(404, "mailbox not found")
    db.query(Domain).filter(Domain.catch_all_mailbox_id == mailbox_id).update(
        {Domain.catch_all_mailbox_id: None}
    )
    db.delete(mailbox)
    db.commit()


# ── Aliases ─────────────────────────────────────────────────────────────────
@router.get("/aliases", response_model=list[AliasOut])
def list_aliases(domain_id: int | None = None, db: Session = Depends(get_db)) -> list[Alias]:
    stmt = select(Alias).order_by(Alias.source)
    if domain_id:
        stmt = stmt.where(Alias.domain_id == domain_id)
    return db.scalars(stmt).all()


@router.post("/aliases", response_model=AliasOut, status_code=201)
def add_alias(payload: AliasCreate, db: Session = Depends(get_db)) -> Alias:
    domain = db.get(Domain, payload.domain_id)
    if not domain:
        raise HTTPException(404, "domain not found")
    source = payload.source.strip().lower()
    if "@" not in source:
        source = f"{source}@{domain.name}"
    if not source.endswith(f"@{domain.name}"):
        raise HTTPException(422, "alias source must be on this domain")
    alias = Alias(domain_id=domain.id, source=source, destination=str(payload.destination).lower())
    db.add(alias)
    db.commit()
    db.refresh(alias)
    return alias


@router.delete("/aliases/{alias_id}", status_code=204)
def delete_alias(alias_id: int, db: Session = Depends(get_db)):
    alias = db.get(Alias, alias_id)
    if not alias:
        raise HTTPException(404, "alias not found")
    db.delete(alias)
    db.commit()
