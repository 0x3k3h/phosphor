"""Contacts, contact lists, and CSV import."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...auth import current_user
from ...database import get_db
from ...models import (
    Contact,
    ContactList,
    ContactListMembership,
    ContactStatus,
    Suppression,
)
from ...schemas import (
    ContactCreate,
    ContactListCreate,
    ContactListOut,
    ContactOut,
    ContactUpdate,
    ImportResult,
)

router = APIRouter(prefix="/api", tags=["contacts"], dependencies=[Depends(current_user)])

_STANDARD_FIELDS = {"email", "first_name", "last_name", "company", "title", "phone", "website", "notes"}
_HEADER_ALIASES = {
    "e-mail": "email", "email address": "email", "mail": "email",
    "firstname": "first_name", "first": "first_name", "given name": "first_name", "fname": "first_name",
    "lastname": "last_name", "last": "last_name", "surname": "last_name", "lname": "last_name",
    "organization": "company", "organisation": "company", "company name": "company", "account": "company",
    "job title": "title", "position": "title", "role": "title",
    "phone number": "phone", "mobile": "phone", "tel": "phone",
    "url": "website", "site": "website", "domain": "website",
}


# ── Contacts ────────────────────────────────────────────────────────────────
@router.get("/contacts", response_model=list[ContactOut])
def list_contacts(
    q: str | None = None,
    status: str | None = None,
    list_id: int | None = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[Contact]:
    stmt = select(Contact).order_by(Contact.created_at.desc()).limit(limit).offset(offset)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Contact.email.ilike(like) | Contact.first_name.ilike(like)
            | Contact.last_name.ilike(like) | Contact.company.ilike(like)
        )
    if status:
        stmt = stmt.where(Contact.status == status)
    if list_id:
        stmt = stmt.join(ContactListMembership, ContactListMembership.contact_id == Contact.id).where(
            ContactListMembership.list_id == list_id
        )
    return db.scalars(stmt).all()


@router.get("/contacts/count")
def count_contacts(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(Contact.status, func.count()).group_by(Contact.status)).all()
    return {"total": sum(n for _s, n in rows), "by_status": {s: n for s, n in rows}}


@router.post("/contacts", response_model=ContactOut, status_code=201)
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)) -> Contact:
    email = str(payload.email).lower()
    if db.scalar(select(Contact).where(Contact.email == email)):
        raise HTTPException(409, "contact already exists")
    contact = Contact(**{**payload.model_dump(), "email": email})
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@router.get("/contacts/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: int, db: Session = Depends(get_db)) -> Contact:
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "contact not found")
    return contact


@router.put("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: int, payload: ContactUpdate, db: Session = Depends(get_db)) -> Contact:
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "contact not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, key, value)
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "contact not found")
    db.delete(contact)
    db.commit()


# ── Lists ───────────────────────────────────────────────────────────────────
@router.get("/lists", response_model=list[ContactListOut])
def list_lists(db: Session = Depends(get_db)) -> list[ContactListOut]:
    lists = db.scalars(select(ContactList).order_by(ContactList.name)).all()
    out = []
    for lst in lists:
        n = db.scalar(
            select(func.count()).select_from(ContactListMembership).where(
                ContactListMembership.list_id == lst.id
            )
        )
        row = ContactListOut.model_validate(lst)
        row.member_count = int(n or 0)
        out.append(row)
    return out


@router.post("/lists", response_model=ContactListOut, status_code=201)
def create_list(payload: ContactListCreate, db: Session = Depends(get_db)) -> ContactListOut:
    if db.scalar(select(ContactList).where(ContactList.name == payload.name)):
        raise HTTPException(409, "list name already used")
    lst = ContactList(name=payload.name, description=payload.description)
    db.add(lst)
    db.commit()
    db.refresh(lst)
    return ContactListOut.model_validate(lst)


@router.delete("/lists/{list_id}", status_code=204)
def delete_list(list_id: int, db: Session = Depends(get_db)):
    lst = db.get(ContactList, list_id)
    if not lst:
        raise HTTPException(404, "list not found")
    db.delete(lst)
    db.commit()


@router.post("/lists/{list_id}/members")
def add_members(list_id: int, contact_ids: list[int], db: Session = Depends(get_db)) -> dict:
    lst = db.get(ContactList, list_id)
    if not lst:
        raise HTTPException(404, "list not found")
    existing = {
        c for (c,) in db.execute(
            select(ContactListMembership.contact_id).where(ContactListMembership.list_id == list_id)
        )
    }
    added = 0
    for cid in contact_ids:
        if cid in existing or not db.get(Contact, cid):
            continue
        db.add(ContactListMembership(list_id=list_id, contact_id=cid))
        added += 1
    db.commit()
    return {"added": added}


@router.delete("/lists/{list_id}/members/{contact_id}", status_code=204)
def remove_member(list_id: int, contact_id: int, db: Session = Depends(get_db)):
    row = db.scalar(
        select(ContactListMembership).where(
            ContactListMembership.list_id == list_id,
            ContactListMembership.contact_id == contact_id,
        )
    )
    if row:
        db.delete(row)
        db.commit()


# ── CSV import ──────────────────────────────────────────────────────────────
def _normalise_header(name: str) -> str:
    key = name.strip().lower()
    key = _HEADER_ALIASES.get(key, key.replace(" ", "_"))
    return key


@router.post("/contacts/import", response_model=ImportResult)
async def import_csv(
    file: UploadFile = File(...),
    list_name: str | None = Query(None),
    update_existing: bool = Query(True),
    tag_suppressed: bool = Query(True),
    db: Session = Depends(get_db),
) -> ImportResult:
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        raise HTTPException(422, "could not read CSV header")

    header_map = {h: _normalise_header(h) for h in reader.fieldnames}
    if "email" not in header_map.values():
        raise HTTPException(422, "CSV needs an 'email' column")

    target_list: ContactList | None = None
    if list_name:
        target_list = db.scalar(select(ContactList).where(ContactList.name == list_name))
        if not target_list:
            target_list = ContactList(name=list_name, description="Imported from CSV")
            db.add(target_list)
            db.flush()

    suppressed = {
        e for (e,) in db.execute(select(Suppression.email))
    } if tag_suppressed else set()

    created = updated = skipped = 0
    errors: list[str] = []
    seen: set[str] = set()

    for i, row in enumerate(reader, start=2):
        record: dict = {"custom": {}}
        for original, value in row.items():
            field = header_map.get(original, _normalise_header(original or ""))
            value = (value or "").strip()
            if field == "email":
                record["email"] = value.lower()
            elif field in _STANDARD_FIELDS:
                record[field] = value
            elif field and value:
                record["custom"][field] = value

        email = record.get("email", "")
        if not email or "@" not in email:
            skipped += 1
            errors.append(f"row {i}: missing/invalid email")
            continue
        if email in seen:
            skipped += 1
            continue
        seen.add(email)

        contact = db.scalar(select(Contact).where(Contact.email == email))
        if contact:
            if update_existing:
                for key, value in record.items():
                    if key == "custom":
                        contact.custom = {**(contact.custom or {}), **value}
                    elif value:
                        setattr(contact, key, value)
                updated += 1
            else:
                skipped += 1
                contact = None
        else:
            contact = Contact(
                source="csv",
                status=ContactStatus.UNSUBSCRIBED.value if email in suppressed else ContactStatus.ACTIVE.value,
                **{k: v for k, v in record.items() if k != "custom"},
                custom=record["custom"],
            )
            db.add(contact)
            db.flush()
            created += 1

        if target_list and contact:
            exists = db.scalar(
                select(ContactListMembership).where(
                    ContactListMembership.list_id == target_list.id,
                    ContactListMembership.contact_id == contact.id,
                )
            )
            if not exists:
                db.add(ContactListMembership(list_id=target_list.id, contact_id=contact.id))

    db.commit()
    return ImportResult(
        created=created, updated=updated, skipped=skipped, errors=errors[:50],
        list_id=target_list.id if target_list else None,
    )
