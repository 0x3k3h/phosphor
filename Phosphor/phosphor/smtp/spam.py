"""A deliberately small heuristic spam scorer for inbound mail.

This is NOT a replacement for rspamd/SpamAssassin. It catches the obvious
stuff and tags a folder. Score >= 5 -> Spam folder.
"""
from __future__ import annotations

import re

from ..mailstore.parser import ParsedMessage

_BAD_PHRASES = (
    "viagra", "cialis", "you have won", "lottery", "nigerian prince",
    "wire transfer", "bitcoin generator", "act now", "risk-free",
    "100% free", "click here now", "limited time offer", "crypto giveaway",
)


def score(parsed: ParsedMessage, *, spf_pass: bool | None = None, dkim_pass: bool | None = None) -> float:
    pts = 0.0
    subject = (parsed.subject or "").lower()
    body = f"{parsed.body_text}\n{parsed.body_html}".lower()

    if subject == "":
        pts += 1.0
    if subject.isupper() and len(subject) > 10:
        pts += 1.5
    if subject.count("!") >= 3:
        pts += 1.0

    hits = sum(1 for p in _BAD_PHRASES if p in subject or p in body)
    pts += hits * 1.8

    if re.search(r"\$\d{3,}", body) and ("free" in body or "winner" in body):
        pts += 2.0

    if not parsed.from_addr or "@" not in parsed.from_addr:
        pts += 2.0

    if parsed.body_html and not parsed.body_text:
        pts += 0.5
    links = len(re.findall(r"https?://", parsed.body_html or ""))
    if links > 20:
        pts += 1.5

    if spf_pass is False:
        pts += 1.5
    if dkim_pass is False:
        pts += 1.0
    if spf_pass and dkim_pass:
        pts -= 1.0

    return max(0.0, round(pts, 2))


def folder_for_score(value: float) -> str:
    return "Spam" if value >= 5.0 else "INBOX"
