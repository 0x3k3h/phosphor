"""Maildir-backed message storage.

Layout on disk:  <maildir_root>/<local_part>@<domain>/{new,cur,tmp}
Sub-folders (Sent, Trash, ...) are Maildir++ folders: `.Sent/{new,cur,tmp}`.
This layout is the same one Dovecot expects, so you can bolt a real IMAP
daemon on later and point it at <maildir_root>.
"""
from __future__ import annotations

import mailbox
import os
from pathlib import Path

# Windows (and FAT) forbid ':' in filenames, which the Maildir "info" suffix
# (`name:2,S`) uses. Python lets us swap the separator; '!' is the conventional
# replacement and is what Dovecot expects when configured for such filesystems.
if os.name == "nt":
    mailbox.Maildir.colon = "!"

_FOLDER_TO_SUB = {
    "INBOX": None,
    "Sent": "Sent",
    "Archive": "Archive",
    "Trash": "Trash",
    "Spam": "Junk",
    "Drafts": "Drafts",
}


class MailStore:
    def __init__(self, maildir_path: str | Path):
        self.root = Path(maildir_path)
        # mailbox.Maildir(create=True) only lays down tmp/new/cur when the
        # maildir path does not yet exist, so ensure them explicitly — this is
        # correct whether the directory is new, half-made, or already a maildir.
        for sub in ("tmp", "new", "cur"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        mailbox.Maildir(str(self.root), create=True).close()

    # ── internal ────────────────────────────────────────────────────────────
    def _box(self, folder: str) -> mailbox.Maildir:
        sub = _FOLDER_TO_SUB.get(folder, folder)
        md = mailbox.Maildir(str(self.root), create=True)
        if sub is None:
            return md
        try:
            return md.get_folder(sub)
        except mailbox.NoSuchMailboxError:
            return md.add_folder(sub)

    # ── public API ──────────────────────────────────────────────────────────
    def add(self, raw: bytes, folder: str = "INBOX", *, seen: bool = False, flagged: bool = False) -> str:
        box = self._box(folder)
        msg = mailbox.MaildirMessage(raw)
        msg.set_subdir("cur" if seen else "new")
        if seen:
            msg.add_flag("S")
        if flagged:
            msg.add_flag("F")
        key = box.add(msg)
        box.flush()
        return key

    def get_bytes(self, folder: str, key: str) -> bytes | None:
        box = self._box(folder)
        try:
            return box.get_bytes(key)
        except KeyError:
            return None

    def set_flags(self, folder: str, key: str, *, seen: bool | None = None, flagged: bool | None = None) -> None:
        box = self._box(folder)
        try:
            msg = box[key]
        except KeyError:
            return
        if seen is not None:
            msg.add_flag("S") if seen else msg.remove_flag("S")
        if flagged is not None:
            msg.add_flag("F") if flagged else msg.remove_flag("F")
        box[key] = msg
        box.flush()

    def move(self, src_folder: str, key: str, dst_folder: str) -> str | None:
        raw = self.get_bytes(src_folder, key)
        if raw is None:
            return None
        new_key = self.add(raw, dst_folder, seen=True)
        self.delete(src_folder, key)
        return new_key

    def delete(self, folder: str, key: str) -> None:
        box = self._box(folder)
        try:
            box.remove(key)
            box.flush()
        except KeyError:
            pass

    def usage_bytes(self) -> int:
        total = 0
        for p in self.root.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        return total
