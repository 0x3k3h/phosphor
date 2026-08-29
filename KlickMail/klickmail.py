#!/usr/bin/env python3
"""
KLICK_MAIL // launcher

A zero-dependency host for the KlickMail client. Serves ./client on
http://127.0.0.1:8765 and opens your browser. The client is a static web app
that talks to a Phosphor mail server over its JSON API — nothing runs here
except a static file server, so any Python 3.8+ works on Windows/macOS/Linux.

    python klickmail.py                       # serve + open browser
    python klickmail.py --port 9000
    python klickmail.py --no-browser
    python klickmail.py --server http://100.x.x.x:8000   # pre-fill the Phosphor endpoint
"""
from __future__ import annotations

import argparse
import functools
import http.server
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import quote

CLIENT_DIR = Path(__file__).resolve().parent / "client"


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # The client stores its token in localStorage; keep every asset fresh so
        # updates land without a hard refresh.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        if "favicon" in (args[0] if args else ""):
            return
        sys.stderr.write("  %s\n" % (fmt % args))


def _free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) != 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="klickmail")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--server", default="", help="pre-fill the Phosphor API endpoint")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not CLIENT_DIR.is_dir():
        print(f"client/ not found next to {__file__}", file=sys.stderr)
        return 1

    port = args.port
    if not _free(port, args.host):
        for candidate in range(port + 1, port + 25):
            if _free(candidate, args.host):
                port = candidate
                break
        else:
            print(f"no free port near {args.port}", file=sys.stderr)
            return 1

    handler = functools.partial(Handler, directory=str(CLIENT_DIR))
    httpd = http.server.ThreadingHTTPServer((args.host, port), handler)

    url = f"http://{args.host}:{port}/"
    if args.server:
        url += f"?server={quote(args.server, safe='')}"

    print(r"""
  _  _____ _____ _____ _  _   __  __   _   ___ _
 | |/ / __\_   _|_   _| |/ /  |  \/  | /_\ |_ _| |
 | ' <| _|   | |   | | | ' <   | |\/| |/ _ \ | || |__
 |_|\_\_|    |_|   |_| |_|\_\  |_|  |_/_/ \_\___|____|
 KLICK_MAIL // V.1.0.0 // desktop client for Phosphor
""")
    print(f"  serving : {url}")
    print(f"  client  : {CLIENT_DIR}")
    print("  stop    : Ctrl-C\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  KLICK_MAIL // stopped")
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
