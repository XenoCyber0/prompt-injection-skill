#!/usr/bin/env python3
"""Minimal canary listener: logs each request line and flags canary tokens.

Usage: python listener.py --port 8000 --out hits.log

Point the skill's exfil payloads at this host (via a tunnel if the target is remote) and
watch hits.log for PI- tokens. Serves an empty 200 for anything so image/link probes fire.
"""
import argparse
import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

RECORD = "hits.log"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"{stamp}\t{self.client_address[0]}\t{self.path}"
        with open(RECORD, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        flag = "  <-- PI CANARY" if "PI-" in self.path else ""
        print(line + flag)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.end_headers()
        self.wfile.write(b"")

    def log_message(self, *args):
        pass


def main():
    global RECORD
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--out", default="hits.log")
    args = ap.parse_args()
    RECORD = args.out
    print(f"listening on :{args.port}, logging to {RECORD}")
    HTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
