"""Assemble outbound MIME messages for both the webmail composer and campaigns."""
from __future__ import annotations

import datetime as dt
import re
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

import bleach

from ..config import settings


def html_to_text(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = bleach.clean(text, tags=[], strip=True)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def build_message(
    *,
    from_addr: str,
    from_name: str = "",
    to: list[str],
    subject: str,
    body_text: str = "",
    body_html: str = "",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str = "",
    in_reply_to: str = "",
    references: str = "",
    list_unsubscribe_url: str = "",
    is_bulk: bool = False,
    extra_headers: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    """Return (raw_bytes, message_id)."""
    msg = EmailMessage()
    domain = from_addr.split("@", 1)[-1] or settings.primary_domain
    message_id = make_msgid(domain=domain)

    msg["Message-ID"] = message_id
    msg["Date"] = formatdate(dt.datetime.now(dt.timezone.utc).timestamp(), usegmt=True)
    msg["From"] = formataddr((from_name, from_addr)) if from_name else from_addr
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject or ""
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip() if references else in_reply_to
    elif references:
        msg["References"] = references

    if list_unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{list_unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if is_bulk:
        msg["Precedence"] = "bulk"
        msg["Auto-Submitted"] = "auto-generated"

    for key, value in (extra_headers or {}).items():
        if key not in msg:
            msg[key] = value

    text_part = body_text or (html_to_text(body_html) if body_html else "")
    if body_html:
        msg.set_content(text_part or " ")
        msg.add_alternative(body_html, subtype="html")
    else:
        msg.set_content(text_part or " ")

    raw = msg.as_bytes()
    return raw, message_id
