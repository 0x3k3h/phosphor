#!/usr/bin/env python3
"""Phosphor entrypoint.

    python run.py              # API + SMTP listeners + background worker
    python run.py --api-only   # just the web API (no SMTP, no worker)
    python run.py --reload     # dev autoreload (implies --api-only)

Environment is read from ./.env  (see .env.example).
"""
from __future__ import annotations

import argparse
import sys

import uvicorn

from phosphor.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(prog="phosphor")
    parser.add_argument("--host", default=settings.api_host)
    parser.add_argument("--port", type=int, default=settings.api_port)
    parser.add_argument("--api-only", action="store_true",
                        help="do not start the SMTP servers or the background worker")
    parser.add_argument("--reload", action="store_true", help="dev autoreload (forces --api-only)")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    run_services = not (args.api_only or args.reload)

    banner = r"""
   ___  _  _  ___  ___ ___ _  _ ___  ___
  | _ \| || |/ _ \/ __| _ \ || / _ \| _ \
  |  _/| __ | (_) \__ \  _/ __ | (_) |   /
  |_|  |_||_|\___/|___/_| |_||_|\___/|_|_\
  self-hosted mail server + cold outreach
"""
    print(banner)
    print(f"  control panel : {settings.public_url}")
    print(f"  API bind      : {args.host}:{args.port}")
    if run_services:
        print(f"  SMTP inbound  : {settings.smtp_inbound_host}:{settings.smtp_inbound_port}")
        print(f"  SMTP submit   : {settings.smtp_inbound_host}:{settings.smtp_submission_port}")
        print(f"  worker        : on (outbound queue + campaign sequencer)")
    else:
        print("  services      : OFF (API only)")
    print(f"  data dir      : {settings.data_dir}\n")

    if args.reload:
        uvicorn.run("phosphor.app:app", host=args.host, port=args.port, reload=True,
                    log_level=args.log_level)
        return 0

    from phosphor.app import create_app

    app = create_app(run_services=run_services)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
