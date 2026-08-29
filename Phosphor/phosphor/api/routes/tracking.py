"""PUBLIC endpoints (no auth): open pixel, click redirect, one-click unsubscribe."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database import session_scope
from ...models import Contact, EmailEvent, EventType, TrackingToken, utcnow
from ...outreach.campaigns import verify_unsubscribe_token
from ...outreach.suppression import suppress
from ...outreach.tracking import PIXEL_GIF

router = APIRouter(tags=["tracking"])

_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"}


def _record(db: Session, token_row: TrackingToken, event_type: str, request: Request, url: str = "") -> None:
    db.add(EmailEvent(
        type=event_type,
        campaign_id=token_row.campaign_id,
        contact_id=token_row.contact_id,
        outbound_message_id=token_row.outbound_message_id,
        step_order=token_row.step_order,
        url=url,
        user_agent=request.headers.get("user-agent", "")[:500],
        ip=(request.client.host if request.client else "")[:64],
        created_at=utcnow(),
    ))


@router.get("/t/o/{token}.gif")
def open_pixel(token: str, request: Request) -> Response:
    with session_scope() as db:
        row = db.scalar(select(TrackingToken).where(TrackingToken.token == token, TrackingToken.kind == "open"))
        if row:
            ua = request.headers.get("user-agent", "").lower()
            # Skip the obvious prefetchers so opens stay meaningful.
            if not any(bot in ua for bot in ("googleimageproxy", "bingpreview")):
                _record(db, row, EventType.OPEN.value, request)
    return Response(content=PIXEL_GIF, media_type="image/gif", headers=_NOCACHE)


@router.get("/t/c/{token}")
def click(token: str, request: Request) -> Response:
    target = "/"
    with session_scope() as db:
        row = db.scalar(select(TrackingToken).where(TrackingToken.token == token, TrackingToken.kind == "click"))
        if row:
            target = row.target_url or "/"
            _record(db, row, EventType.CLICK.value, request, url=target)
            # A click implies an open.
            _record(db, row, EventType.OPEN.value, request)
    return RedirectResponse(target, status_code=302, headers=_NOCACHE)


_UNSUB_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Unsubscribe</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{background:#050805;color:#c8f7d4;font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;
      display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
 .card{{background:#0c130c;border:1px solid #1e3b23;border-radius:14px;padding:40px;max-width:460px;
       box-shadow:0 0 40px rgba(0,255,120,.08)}}
 h1{{color:#39ff88;margin:0 0 12px;font-size:20px}}
 button{{background:#39ff88;color:#04170b;border:0;border-radius:8px;padding:12px 20px;font-weight:700;
        cursor:pointer;font-size:15px}}
 .done{{color:#39ff88;font-weight:700}}
 code{{color:#8ad9a0}}
</style></head><body><div class="card">{body}</div>
<script>
 const f=document.getElementById('f');
 if(f)f.addEventListener('submit',async e=>{{e.preventDefault();
   const r=await fetch(location.pathname,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'confirm=1'}});
   document.querySelector('.card').innerHTML = r.ok
     ? '<h1>Done</h1><p class="done">You have been unsubscribed. You will not receive further emails from this campaign.</p>'
     : '<h1>Something went wrong</h1><p>Please reply to the email and ask to be removed.</p>';
 }});
</script></body></html>"""


def _do_unsubscribe(token: str) -> bool:
    parsed = verify_unsubscribe_token(token)
    if not parsed:
        return False
    campaign_id, contact_id = parsed
    with session_scope() as db:
        contact = db.get(Contact, contact_id)
        if not contact:
            return False
        suppress(db, contact.email, reason="unsubscribe", campaign_id=campaign_id, note="one-click / link")
    return True


@router.get("/u/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str) -> HTMLResponse:
    parsed = verify_unsubscribe_token(token)
    if not parsed:
        return HTMLResponse(_UNSUB_PAGE.format(body="<h1>Invalid link</h1><p>This unsubscribe link is not valid.</p>"),
                            status_code=404)
    campaign_id, contact_id = parsed
    email = ""
    with session_scope() as db:
        contact = db.get(Contact, contact_id)
        email = contact.email if contact else ""
    body = (
        f"<h1>Unsubscribe</h1><p>Remove <code>{email}</code> from this mailing list?</p>"
        f"<form id='f'><button type='submit'>Unsubscribe me</button></form>"
    )
    return HTMLResponse(_UNSUB_PAGE.format(body=body))


@router.post("/u/{token}")
def unsubscribe_submit(token: str) -> Response:
    ok = _do_unsubscribe(token)
    return Response(status_code=200 if ok else 400)


# RFC 8058 List-Unsubscribe=One-Click posts here too (same path handled above).
