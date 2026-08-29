"""MIME parsing helpers — turn raw bytes into something the API and the
reply-detector can use."""
from __future__ import annotations

import email
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import getaddresses, parsedate_to_datetime
import datetime as dt

import bleach

_ALLOWED_TAGS = sorted(
    set(bleach.sanitizer.ALLOWED_TAGS)
    | {
        "p", "br", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6", "img",
        "table", "thead", "tbody", "tr", "td", "th", "pre", "hr", "blockquote",
        "u", "s", "sub", "sup", "font", "center",
    }
)
_ALLOWED_ATTRS = {
    "*": ["style", "class", "align", "width", "height", "dir"],
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "width", "height"],
    "font": ["color", "face", "size"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto", "cid", "data"]


def _clean_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int
    content_id: str = ""


@dataclass
class ParsedMessage:
    message_id: str = ""
    in_reply_to: str = ""
    references: str = ""
    from_addr: str = ""
    from_name: str = ""
    to_addrs: list[str] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)
    subject: str = ""
    date: dt.datetime | None = None
    body_text: str = ""
    body_html: str = ""
    snippet: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)
    size_bytes: int = 0

    @property
    def sanitized_html(self) -> str:
        if not self.body_html:
            return ""
        return bleach.clean(
            self.body_html,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRS,
            protocols=_ALLOWED_PROTOCOLS,
            strip=True,
        )


def _addr_list(msg: EmailMessage, header: str) -> list[str]:
    raw = msg.get_all(header, [])
    return [addr.lower() for _name, addr in getaddresses(raw) if addr]


def parse_message(raw: bytes) -> ParsedMessage:
    msg: EmailMessage = email.message_from_bytes(raw, policy=default_policy)  # type: ignore[assignment]
    out = ParsedMessage(size_bytes=len(raw))

    out.message_id = (msg.get("Message-ID") or "").strip()
    out.in_reply_to = (msg.get("In-Reply-To") or "").strip()
    out.references = (msg.get("References") or "").strip()
    out.subject = _clean_header(msg.get("Subject"))

    from_pairs = getaddresses(msg.get_all("From", []))
    if from_pairs:
        out.from_name = _clean_header(from_pairs[0][0])
        out.from_addr = (from_pairs[0][1] or "").lower()
    out.to_addrs = _addr_list(msg, "To")
    out.cc_addrs = _addr_list(msg, "Cc")

    try:
        out.date = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
    except (TypeError, ValueError):
        out.date = None

    out.headers = {k: _clean_header(v) for k, v in msg.items()}

    # Bodies + attachments
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            disp = (part.get_content_disposition() or "").lower()
            if disp == "attachment" or (part.get_filename() and ctype not in ("text/plain", "text/html")):
                payload = part.get_payload(decode=True) or b""
                out.attachments.append(
                    Attachment(
                        filename=_clean_header(part.get_filename()) or "attachment",
                        content_type=ctype,
                        size=len(payload),
                        content_id=(part.get("Content-ID") or "").strip("<>"),
                    )
                )
            elif ctype == "text/plain" and not out.body_text:
                out.body_text = part.get_content()
            elif ctype == "text/html" and not out.body_html:
                out.body_html = part.get_content()
    else:
        if msg.get_content_type() == "text/html":
            out.body_html = msg.get_content()
        else:
            out.body_text = msg.get_content()

    basis = out.body_text or bleach.clean(out.body_html, tags=[], strip=True)
    out.snippet = " ".join(basis.split())[:240]
    return out
