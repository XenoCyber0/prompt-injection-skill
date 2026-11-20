#!/usr/bin/env python3
"""Replay prompt-injection cases against a local callable or an HTTP endpoint.

Usage:
  python run_harness.py --callable myapp:target --cases cases.jsonl --out results.jsonl
  python run_harness.py --url https://staging.app/api/chat --cases cases.jsonl \
      --bearer "$TOKEN" --limit 20 --delay 1.0

Each case is one JSON object per line (see cases.jsonl). The case `entry_point` is passed
to local targets and (unless --bare) included in the HTTP JSON body so routing survives.

HTTP mode sends `{<field>: payload}` (default field: message) plus `entry_point`. Every
response is capped at --max-bytes and a 4xx/5xx is recorded as the result, not an error,
so score.py can still see a reflected canary.
"""
import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

REQUIRED = ("id", "entry_point", "payload")

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "prompt-injection-generator/0.1 (+harness)",
}


def load_cases(path):
    cases, problems = [], []
    with open(path, encoding="utf-8-sig") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"line {n}: {exc}")
                continue
            missing = [k for k in REQUIRED if not case.get(k)]
            if missing:
                problems.append(f"line {n}: missing {', '.join(missing)}")
                continue
            cases.append(case)
    if problems:
        raise SystemExit("bad cases file:\n  " + "\n  ".join(problems))
    return cases


def import_callable(spec):
    module, _, attr = spec.partition(":")
    if not module or not attr:
        raise SystemExit("--callable must be module:function")
    __import__(module)
    return getattr(sys.modules[module], attr)


RESULT_KEYS = ("answer", "requests", "tool_args", "tool_calls")


def shape_response(text, status):
    """Record the body, lifting agent telemetry if the endpoint already returns it.

    A plain chat endpoint returns prose; that becomes `answer`. An agent endpoint that
    returns {"answer": ..., "tool_calls": [...]} is passed through, so score.py can see
    the call sequence instead of guessing from prose.
    """
    out = {"answer": text, "requests": [], "status": status}
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return out
    if not isinstance(data, dict):
        return out
    lifted = {k: data[k] for k in RESULT_KEYS if k in data}
    if not lifted:
        return out
    out.update(lifted)
    if not isinstance(out["answer"], str):
        out["answer"] = json.dumps(out["answer"])
    return out


def build_opener(insecure):
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


def make_http_target(args):
    parsed = urlparse(args.url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SystemExit("--url must be an absolute http(s) URL")
    opener = build_opener(args.insecure)
    headers = dict(DEFAULT_HEADERS)
    for h in args.header:
        key, sep, val = h.partition(":")
        if not sep:
            raise SystemExit(f"--header must be 'Name: value', got {h!r}")
        headers[key.strip()] = val.strip()
    if args.bearer:
        headers["Authorization"] = f"Bearer {args.bearer}"

    def target(entry_point, payload):
        body = {"entry_point": entry_point, args.field: payload}
        if args.bare:
            body = {args.field: payload}
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(args.url, data=data, headers=headers, method=args.method)
        try:
            with opener.open(req, timeout=args.timeout) as resp:
                text = resp.read(args.max_bytes).decode("utf-8", "replace")
                return shape_response(text, resp.status)
        except urllib.error.HTTPError as exc:
            text = exc.read(args.max_bytes).decode("utf-8", "replace")
            return shape_response(text, exc.code)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"transport error: {exc.reason}") from exc

    return target


def run_case(target, case):
    try:
        out = target(case["entry_point"], case["payload"])
    except Exception as exc:  # a crash is itself a result
        return {"id": case["id"], "ok": False, "error": repr(exc), "case": case}
    return {"id": case["id"], "ok": True, "result": out, "case": case}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", default="results.jsonl")
    ap.add_argument("--url")
    ap.add_argument("--callable")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--max-bytes", type=int, default=1_000_000)
    ap.add_argument("--field", default="message")
    ap.add_argument("--method", default="POST")
    ap.add_argument("--header", action="append", default=[], metavar="NAME:VALUE")
    ap.add_argument("--bearer", metavar="TOKEN")
    ap.add_argument("--bare", action="store_true", help="omit entry_point from the body")
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification")
    ap.add_argument("--fail-fast", action="store_true", help="stop on first transport error")
    args = ap.parse_args()

    if bool(args.url) == bool(args.callable):
        raise SystemExit("give exactly one of --url or --callable")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be >= 1")

    cases = load_cases(args.cases)
    if args.limit is not None:
        cases = cases[: args.limit]

    target = make_http_target(args) if args.url else import_callable(args.callable)

    print(f"running {len(cases)} case(s)", file=sys.stderr)
    with open(args.out, "w", encoding="utf-8") as fh:
        for i, case in enumerate(cases, 1):
            rec = run_case(target, case)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            status = ""
            if rec["ok"]:
                st = (rec.get("result") or {}).get("status")
                status = f" [{st}]" if st else ""
            print(f"[{i}/{len(cases)}] {case['id']} -> {'ok' + status if rec['ok'] else 'ERR'}", file=sys.stderr)
            if args.fail_fast and not rec["ok"]:
                break
            if args.delay and i < len(cases):
                time.sleep(args.delay)
    print(f"wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
