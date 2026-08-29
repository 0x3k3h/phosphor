"""Provisioning helpers shared by the API routes."""
from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import hash_password
from ..crypto.dkim import dkim_txt_value, generate_keypair
from ..mailstore import MailStore
from ..models import Domain, Mailbox

_LOCALPART_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})+$")


def create_domain(db: Session, name: str, selector: str = "phosphor") -> Domain:
    name = name.strip().lower().rstrip(".")
    if not _DOMAIN_RE.match(name):
        raise HTTPException(422, f"'{name}' is not a valid domain name")
    if db.scalar(select(Domain).where(Domain.name == name)):
        raise HTTPException(409, f"domain '{name}' already exists")

    private_pem, public_b64 = generate_keypair()
    domain = Domain(
        name=name,
        dkim_selector=selector or settings.dkim_selector,
        dkim_private_key=private_pem,
        dkim_public_key=public_b64,
        dkim_txt_value=dkim_txt_value(public_b64),
    )
    db.add(domain)
    db.flush()
    return domain


def rotate_dkim(db: Session, domain: Domain) -> Domain:
    private_pem, public_b64 = generate_keypair()
    domain.dkim_private_key = private_pem
    domain.dkim_public_key = public_b64
    domain.dkim_txt_value = dkim_txt_value(public_b64)
    db.flush()
    return domain


def create_mailbox(
    db: Session, *, domain_id: int, local_part: str, password: str,
    display_name: str = "", quota_mb: int = 2048,
) -> Mailbox:
    local_part = local_part.strip().lower()
    if not _LOCALPART_RE.match(local_part):
        raise HTTPException(422, f"'{local_part}' is not a valid local part")
    domain = db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(404, "domain not found")

    address = f"{local_part}@{domain.name}"
    if db.scalar(select(Mailbox).where(Mailbox.address == address)):
        raise HTTPException(409, f"mailbox '{address}' already exists")

    maildir_path = settings.maildir_root / address
    MailStore(maildir_path)  # creates the Maildir tree on disk

    mailbox = Mailbox(
        domain_id=domain.id,
        local_part=local_part,
        address=address,
        display_name=display_name,
        password_hash=hash_password(password),
        quota_bytes=quota_mb * 1024 * 1024,
        maildir_path=str(maildir_path),
    )
    db.add(mailbox)
    db.flush()
    return mailbox
