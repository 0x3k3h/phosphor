"""Open-pixel and click-wrapping for campaign mail.

Tokens are random 32-char strings stored in `tracking_tokens`. The open pixel
is served from `<public_url>/t/o/<token>.gif`; wrapped links redirect through
`<public_url>/t/c/<token>`.
"""
from __future__ import annotations

import re
import secrets

from sqlalchemy.orm import Session

from ..config import settings
from ..models import TrackingToken

# 1x1 transparent GIF
PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000ffffff00000021f90401000000002c00000000"
    "010001000002024401003b"
)

_HREF_RE = re.compile(r'(<a\b[^>]*?\bhref=)(["\'])(https?://[^"\']+)\2', re.IGNORECASE)


def _new_token() -> str:
    return secrets.token_urlsafe(24)[:32]


def _base() -> str:
    return settings.public_url.rstrip("/")


def make_open_pixel(
    db: Session, *, campaign_id: int, contact_id: int, outbound_message_id: int | None, step_order: int
) -> str:
    token = _new_token()
    db.add(TrackingToken(
        token=token, kind="open", campaign_id=campaign_id, contact_id=contact_id,
        outbound_message_id=outbound_message_id, step_order=step_order,
    ))
    return f'<img src="{_base()}/t/o/{token}.gif" width="1" height="1" alt="" style="display:none" />'


def wrap_links(
    db: Session, html: str, *, campaign_id: int, contact_id: int,
    outbound_message_id: int | None, step_order: int,
) -> str:
    if not html:
        return html

    def _repl(m: re.Match[str]) -> str:
        prefix, quote, url = m.group(1), m.group(2), m.group(3)
        if "/t/c/" in url or "/t/o/" in url or "unsubscribe" in url.lower():
            return m.group(0)
        token = _new_token()
        db.add(TrackingToken(
            token=token, kind="click", target_url=url, campaign_id=campaign_id,
            contact_id=contact_id, outbound_message_id=outbound_message_id, step_order=step_order,
        ))
        return f'{prefix}{quote}{_base()}/t/c/{token}{quote}'

    return _HREF_RE.sub(_repl, html)


def inject_open_pixel(html: str, pixel: str) -> str:
    if not html:
        return pixel
    if "</body>" in html.lower():
        idx = html.lower().rindex("</body>")
        return html[:idx] + pixel + html[idx:]
    return html + pixel
