#!/usr/bin/env python3
"""Replay prompt-injection cases against a local callable or an HTTP endpoint.

Usage:
  python run_harness.py --callable myapp:target --cases cases.jsonl --out results.jsonl
  python run_harness.py --url https://staging.app/api/chat --cases cases.jsonl --limit 20 --delay 1.0

Each case is one JSON object per line (see cases.example.json). The target records any
outbound requests it observes so score.py can detect exfil, not just answer text.
"""
import argparse
import json
import sys
import time

try:
    import urllib.request
except ImportError:  # pragma: no cover
    urllib = None


def load_cases(path):
    cases = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def import_callable(spec):
    module, _, attr = spec.partition(":")
    if not attr:
        raise SystemExit("--callable must be module:function")
    __import__(module)
    return getattr(sys.modules[module], attr)


def http_target(url, payload, timeout):
    body = json.dumps({"message": payload}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {"answer": resp.read().decode("utf-8", "replace"), "requests": []}


def run_case(target, case):
    try:
        out = target(case["entry_point"], case["payload"])
    except Exception as exc:  # keep going; a crash is itself a result
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
    args = ap.parse_args()

    if bool(args.url) == bool(args.callable):
        raise SystemExit("give exactly one of --url or --callable")

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]

    if args.url:
        def call(entry_point, payload):
            return http_target(args.url, payload, args.timeout)
        target = call
    else:
        target = import_callable(args.callable)

    print(f"running {len(cases)} case(s)", file=sys.stderr)
    with open(args.out, "w", encoding="utf-8") as fh:
        for i, case in enumerate(cases, 1):
            rec = run_case(target, case)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"[{i}/{len(cases)}] {case['id']} -> {'ok' if rec['ok'] else 'ERR'}", file=sys.stderr)
            if args.delay:
                time.sleep(args.delay)


if __name__ == "__main__":
    main()
