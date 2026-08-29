"""Reusable message templates + a live personalization preview."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...auth import current_user
from ...database import get_db
from ...models import Contact, Template
from ...outreach import personalization as pz
from ...schemas import (
    RenderPreviewRequest,
    RenderPreviewResponse,
    TemplateCreate,
    TemplateOut,
)

router = APIRouter(prefix="/api/templates", tags=["templates"], dependencies=[Depends(current_user)])


@router.get("", response_model=list[TemplateOut])
def list_templates(db: Session = Depends(get_db)) -> list[Template]:
    return db.scalars(select(Template).order_by(Template.name)).all()


@router.post("", response_model=TemplateOut, status_code=201)
def create_template(payload: TemplateCreate, db: Session = Depends(get_db)) -> Template:
    if db.scalar(select(Template).where(Template.name == payload.name)):
        raise HTTPException(409, "template name already used")
    tpl = Template(**payload.model_dump())
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.get("/{template_id}", response_model=TemplateOut)
def get_template(template_id: int, db: Session = Depends(get_db)) -> Template:
    tpl = db.get(Template, template_id)
    if not tpl:
        raise HTTPException(404, "template not found")
    return tpl


@router.put("/{template_id}", response_model=TemplateOut)
def update_template(template_id: int, payload: TemplateCreate, db: Session = Depends(get_db)) -> Template:
    tpl = db.get(Template, template_id)
    if not tpl:
        raise HTTPException(404, "template not found")
    for key, value in payload.model_dump().items():
        setattr(tpl, key, value)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db)):
    tpl = db.get(Template, template_id)
    if not tpl:
        raise HTTPException(404, "template not found")
    db.delete(tpl)
    db.commit()


@router.post("/preview", response_model=RenderPreviewResponse)
def preview(payload: RenderPreviewRequest, db: Session = Depends(get_db)) -> RenderPreviewResponse:
    if payload.contact_id:
        contact = db.get(Contact, payload.contact_id)
        if not contact:
            raise HTTPException(404, "contact not found")
        ctx = pz.context_for_contact(contact, payload.sample)
    else:
        base = Contact(
            email="jordan@acme.example", first_name="Jordan", last_name="Lee",
            company="Acme", title="Head of Growth", website="acme.example",
        )
        ctx = pz.context_for_contact(base, payload.sample)

    seed = f"preview:{payload.contact_id or 0}"
    subject, body_html, body_text = pz.render_all(
        subject=payload.subject, body_html=payload.body_html, body_text=payload.body_text,
        ctx=ctx, seed=seed,
    )
    missing = sorted(
        set(pz.missing_tags(payload.subject, ctx))
        | set(pz.missing_tags(payload.body_html, ctx))
        | set(pz.missing_tags(payload.body_text, ctx))
    )
    return RenderPreviewResponse(
        subject=subject, body_html=body_html, body_text=body_text, missing_tags=missing
    )
