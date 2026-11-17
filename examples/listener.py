#!/usr/bin/env python3
"""Canary listener: logs every request and flags PI- canary tokens.

Usage:
  python listener.py --port 8000 --out hits.log            # local targets
  python listener.py --port 8000 --host 0.0.0.0            # behind ngrok/cloudflared
  cloudflared tunnel --url http://localhost:8000 &

Binds 127.0.0.1 by default so it is not exposed to the LAN. Serves a valid 1x1 PNG for any
request so markdown-image exfil actually completes and hits are logged.
"""
import argparse
import base64
import datetime
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORD = "hits.log"
QUIET = False
LOCK = threading.Lock()
MAX_PATH = 2048

# transparent 1x1 PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def clean(text, limit=MAX_PATH):
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
    return text[:limit]


class Handler(BaseHTTPRequestHandler):
    server_version = "pi-listener/0.1"

    def _record(self):
        path = clean(self.path)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        ua = clean(self.headers.get("User-Agent", "-"), 120)
        line = f"{stamp}\t{clean(self.client_address[0])}\t{path}\t{ua}"
        with LOCK:
            with open(RECORD, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        if not QUIET:
            flag = "  <-- PI CANARY" if "PI-" in self.path else ""
            print(line + flag, flush=True)

    def _respond(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        self._record()
        self._respond(PNG)

    def do_HEAD(self):
        self._record()
        self._respond(PNG)

    def do_POST(self):
        # drain any body so probes that POST don't stall the client
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length:
            self.rfile.read(min(length, 1 << 20))
        self._record()
        self._respond(PNG)

    def log_message(self, *args):
        pass


def main():
    global RECORD, QUIET
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to expose for a tunnel")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--out", default="hits.log")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    RECORD = args.out
    QUIET = args.quiet
    bind = f"{args.host}:{args.port}"
    print(f"listening on {bind}, logging to {RECORD}")
    if args.host == "0.0.0.0":
        print("note: bound to all interfaces — put it behind a tunnel or firewall")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
