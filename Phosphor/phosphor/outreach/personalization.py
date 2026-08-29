"""Merge-tag rendering and spintax for outreach copy.

Merge tags:   {{first_name}}            -> contact field, blank if missing
              {{first_name|there}}      -> fallback when the field is empty
              {{company | your team}}   -> whitespace around the pipe is fine
Custom data:  any key in Contact.custom is addressable by name.
Spintax:      {quick|brief|fast}        -> one option chosen deterministically
                                           per-recipient (stable across retries)
"""
from __future__ import annotations

import hashlib
import random
import re

from ..models import Contact

_TAG_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*(?:\|\s*([^}]*?))?\s*\}\}")
_SPIN_RE = re.compile(r"\{([^{}|]+(?:\|[^{}]+)+)\}")


def context_for_contact(contact: Contact, extra: dict | None = None) -> dict[str, str]:
    ctx: dict[str, str] = {
        "email": contact.email or "",
        "first_name": contact.first_name or "",
        "last_name": contact.last_name or "",
        "full_name": (f"{contact.first_name} {contact.last_name}").strip(),
        "company": contact.company or "",
        "title": contact.title or "",
        "phone": contact.phone or "",
        "website": contact.website or "",
    }
    for key, value in (contact.custom or {}).items():
        ctx[str(key)] = "" if value is None else str(value)
    for key, value in (extra or {}).items():
        ctx[str(key)] = "" if value is None else str(value)
    return ctx


def find_tags(text: str) -> set[str]:
    return {m.group(1) for m in _TAG_RE.finditer(text or "")}


def missing_tags(text: str, ctx: dict[str, str]) -> list[str]:
    out = []
    for m in _TAG_RE.finditer(text or ""):
        name, fallback = m.group(1), m.group(2)
        if not ctx.get(name) and fallback is None:
            out.append(name)
    return sorted(set(out))


def _render_spintax(text: str, rng: random.Random) -> str:
    # Resolve nested spintax from the inside out.
    while True:
        m = _SPIN_RE.search(text)
        if not m:
            return text
        options = m.group(1).split("|")
        text = text[: m.start()] + rng.choice(options) + text[m.end():]


def render(text: str, ctx: dict[str, str], *, seed: str = "") -> str:
    if not text:
        return ""

    def _sub(m: re.Match[str]) -> str:
        name, fallback = m.group(1), m.group(2)
        val = ctx.get(name, "")
        if val:
            return val
        return (fallback or "").strip()

    # Merge tags first (their {{a|b}} form would otherwise look like spintax),
    # then resolve spintax on what's left.
    text = _TAG_RE.sub(_sub, text)
    rng = random.Random(hashlib.sha256(f"{seed}|{text}".encode()).hexdigest())
    return _render_spintax(text, rng)


def render_all(
    *, subject: str, body_html: str, body_text: str, ctx: dict[str, str], seed: str = ""
) -> tuple[str, str, str]:
    return (
        render(subject, ctx, seed=seed + "s"),
        render(body_html, ctx, seed=seed + "h"),
        render(body_text, ctx, seed=seed + "t"),
    )
