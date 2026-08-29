"""Domain management + DNS record generation/verification."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...auth import current_user
from ...crypto.dkim import dkim_txt_split
from ...database import get_db
from ...dns.records import build_records, check_domain
from ...models import Domain, Mailbox, User, utcnow
from ...schemas import DNSRecord, DomainCreate, DomainDNS, DomainOut
from ..service import create_domain, rotate_dkim

router = APIRouter(prefix="/api/domains", tags=["domains"], dependencies=[Depends(current_user)])


@router.get("", response_model=list[DomainOut])
def list_domains(db: Session = Depends(get_db)) -> list[Domain]:
    return db.scalars(select(Domain).order_by(Domain.created_at)).all()


@router.post("", response_model=DomainOut, status_code=201)
def add_domain(payload: DomainCreate, db: Session = Depends(get_db)) -> Domain:
    domain = create_domain(db, payload.name, payload.dkim_selector)
    db.commit()
    db.refresh(domain)
    return domain


@router.get("/{domain_id}", response_model=DomainOut)
def get_domain(domain_id: int, db: Session = Depends(get_db)) -> Domain:
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(404, "domain not found")
    return domain


@router.delete("/{domain_id}", status_code=204)
def delete_domain(domain_id: int, db: Session = Depends(get_db)):
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(404, "domain not found")
    db.delete(domain)
    db.commit()


@router.get("/{domain_id}/dns", response_model=DomainDNS)
def domain_dns(domain_id: int, verify: bool = False, db: Session = Depends(get_db)) -> DomainDNS:
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(404, "domain not found")

    checks = check_domain(domain) if verify else {}
    records: list[DNSRecord] = []
    for rec in build_records(domain):
        check = checks.get(rec["kind"], {})
        records.append(DNSRecord(
            kind=rec["kind"], host=rec["host"], type=rec["type"], value=rec["value"],
            priority=rec.get("priority"), required=rec.get("required", True),
            ok=check.get("ok"), detail=check.get("detail", rec.get("help", "")),
        ))
    if verify:
        domain.dns_status = {k: v.get("ok") for k, v in checks.items() if not k.startswith("_")}
        domain.dns_last_checked_at = utcnow()
        db.commit()
    return DomainDNS(domain=domain.name, records=records)


@router.get("/{domain_id}/dkim")
def domain_dkim(domain_id: int, db: Session = Depends(get_db)) -> dict:
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(404, "domain not found")
    return {
        "selector": domain.dkim_selector,
        "host": f"{domain.dkim_selector}._domainkey.{domain.name}",
        "txt_value": domain.dkim_txt_value,
        "txt_split": dkim_txt_split(domain.dkim_public_key),
        "public_key": domain.dkim_public_key,
    }


@router.post("/{domain_id}/rotate-dkim", response_model=DomainOut)
def rotate(domain_id: int, db: Session = Depends(get_db)) -> Domain:
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(404, "domain not found")
    rotate_dkim(db, domain)
    db.commit()
    db.refresh(domain)
    return domain


@router.put("/{domain_id}/catch-all", response_model=DomainOut)
def set_catch_all(domain_id: int, mailbox_id: int | None = None, db: Session = Depends(get_db)) -> Domain:
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(404, "domain not found")
    if mailbox_id is not None:
        mb = db.get(Mailbox, mailbox_id)
        if not mb or mb.domain_id != domain.id:
            raise HTTPException(422, "mailbox must belong to this domain")
    domain.catch_all_mailbox_id = mailbox_id
    db.commit()
    db.refresh(domain)
    return domain
