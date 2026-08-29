"""Small command-line helpers.

    python -m phosphor.cli create-admin you@example.com [--password ...]
    python -m phosphor.cli reset-password you@example.com
    python -m phosphor.cli list-admins
    python -m phosphor.cli send-queue          # run one outbound-queue pass
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .crypto import hash_password
from .database import init_db, session_scope
from .models import User, utcnow


def _create_admin(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Password (min 10 chars): ")
    if len(password) < 10:
        print("password too short", file=sys.stderr)
        return 1
    with session_scope() as db:
        if db.query(User).filter(User.email == args.email.lower()).first():
            print("that email already exists", file=sys.stderr)
            return 1
        db.add(User(
            email=args.email.lower(),
            password_hash=hash_password(password),
            display_name=args.email.split("@")[0],
            is_admin=True,
        ))
    print(f"created admin {args.email}")
    return 0


def _reset_password(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("New password: ")
    if len(password) < 10:
        print("password too short", file=sys.stderr)
        return 1
    with session_scope() as db:
        user = db.query(User).filter(User.email == args.email.lower()).first()
        if not user:
            print("no such user", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        user.last_login_at = None
    print("password updated")
    return 0


def _list_admins(_args: argparse.Namespace) -> int:
    with session_scope() as db:
        for u in db.query(User).order_by(User.created_at):
            flag = "admin" if u.is_admin else "user"
            active = "active" if u.is_active else "disabled"
            print(f"{u.email:40s} {flag:6s} {active}")
    return 0


def _send_queue(_args: argparse.Namespace) -> int:
    from .smtp import sender

    with session_scope() as db:
        result = sender.process_queue(db, limit=100)
    print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    init_db()
    parser = argparse.ArgumentParser(prog="phosphor.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-admin")
    p.add_argument("email")
    p.add_argument("--password")
    p.set_defaults(func=_create_admin)

    p = sub.add_parser("reset-password")
    p.add_argument("email")
    p.add_argument("--password")
    p.set_defaults(func=_reset_password)

    p = sub.add_parser("list-admins")
    p.set_defaults(func=_list_admins)

    p = sub.add_parser("send-queue")
    p.set_defaults(func=_send_queue)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
