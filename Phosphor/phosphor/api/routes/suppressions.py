"""The do-not-contact list."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...auth import current_user
from ...database import get_db
from ...models import Suppression
from ...outreach.suppression import suppress, unsuppress
from ...schemas import SuppressionCreate, SuppressionOut

router = APIRouter(prefix="/api/suppressions", tags=["suppressions"], dependencies=[Depends(current_user)])


@router.get("", response_model=list[SuppressionOut])
def list_suppressions(q: str | None = None, limit: int = Query(200, le=1000), db: Session = Depends(get_db)):
    stmt = select(Suppression).order_by(Suppression.created_at.desc()).limit(limit)
    if q:
        stmt = stmt.where(Suppression.email.ilike(f"%{q}%"))
    return db.scalars(stmt).all()


@router.post("", response_model=SuppressionOut, status_code=201)
def add_suppression(payload: SuppressionCreate, db: Session = Depends(get_db)) -> Suppression:
    row = suppress(db, str(payload.email), reason=payload.reason, note=payload.note)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{email}", status_code=204)
def remove_suppression(email: str, db: Session = Depends(get_db)):
    unsuppress(db, email)
    db.commit()


@router.post("/import")
async def import_suppressions(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    added = 0
    for line in csv.reader(io.StringIO(raw)):
        if not line:
            continue
        candidate = line[0].strip().lower()
        if "@" in candidate and not candidate.startswith("email"):
            suppress(db, candidate, reason="manual", note="bulk import")
            added += 1
    db.commit()
    return {"added": added}


@router.get("/export", response_class=PlainTextResponse)
def export_suppressions(db: Session = Depends(get_db)) -> str:
    rows = db.scalars(select(Suppression.email).order_by(Suppression.email)).all()
    return "\n".join(rows)
