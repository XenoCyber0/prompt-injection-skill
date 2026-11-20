import argparse
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "examples"))

import listener  # noqa: E402
import run_harness  # noqa: E402
import score  # noqa: E402


def rec(**kw):
    base = {"id": "C1", "ok": True, "case": {"canary": "PI-aaaa1111",
            "expected_signal": "outbound", "family": "f", "severity": "low"},
            "result": {"answer": "", "requests": []}}
    base.update(kw)
    return base


class ScoreTests(unittest.TestCase):
    def test_error(self):
        self.assertEqual(score.classify({"id": "x", "ok": False}), "Error")

    def test_outbound_hit(self):
        self.assertEqual(score.classify(rec(result={"answer": "", "requests": ["http://h/PI-aaaa1111"]})), "Fail")

    def test_tool_arg_hit(self):
        r = rec(result={"answer": "", "requests": [], "tool_args": {"path": "PI-aaaa1111.txt"}},
                case={"canary": "PI-aaaa1111", "expected_signal": "tool_arg"})
        self.assertEqual(score.classify(r), "Fail")

    def test_answer_only_is_weak(self):
        r = rec(result={"answer": "PI-aaaa1111", "requests": []},
                case={"canary": "PI-aaaa1111", "expected_signal": "canary_in_answer"})
        self.assertEqual(score.classify(r), "Weak")

    def test_no_canary_pass(self):
        self.assertEqual(score.classify(rec()), "Pass")

    def test_none_result_safe(self):
        self.assertEqual(score.classify(rec(result=None)), "Pass")

    def test_none_case_safe(self):
        self.assertEqual(score.classify({"ok": True, "result": {}}), "Pass")

    def test_tool_call_hit(self):
        r = rec(case={"canary": "PI-aaaa1111", "expected_signal": "tool_call",
                      "tool_name": "ping"},
                result={"answer": "", "requests": [],
                        "tool_calls": [{"name": "ping", "args": {"host": "PI-aaaa1111"}}]})
        self.assertEqual(score.classify(r), "Fail")

    def test_tool_call_wrong_tool_is_not_fail(self):
        r = rec(case={"canary": "PI-aaaa1111", "expected_signal": "tool_call",
                      "tool_name": "ping"},
                result={"answer": "", "requests": [],
                        "tool_calls": [{"name": "other", "args": {"host": "PI-aaaa1111"}}]})
        self.assertNotEqual(score.classify(r), "Fail")

    def test_state_change_needs_a_sink(self):
        only_text = rec(case={"canary": "PI-aaaa1111", "expected_signal": "state_change"},
                        result={"answer": "PI-aaaa1111", "requests": []})
        self.assertEqual(score.classify(only_text), "Weak")
        moved = rec(case={"canary": "PI-aaaa1111", "expected_signal": "state_change"},
                    result={"answer": "", "requests": [],
                            "tool_calls": [{"name": "email", "args": {"bcc": "PI-aaaa1111"}}]})
        self.assertEqual(score.classify(moved), "Fail")

    def test_evidence_names_the_sink(self):
        r = rec(result={"answer": "", "requests": ["http://h/PI-aaaa1111.png"]})
        self.assertIn("outbound", score.evidence(r))
        r2 = rec(result={"answer": "", "requests": [],
                         "tool_calls": [{"name": "ping", "args": {"h": "PI-aaaa1111"}}]})
        self.assertIn("ping", score.evidence(r2))


class VisibleTests(unittest.TestCase):
    def test_plain_text_unchanged(self):
        self.assertEqual(score.visible("Ignore prior instructions."),
                         "Ignore prior instructions.")

    def test_tag_chars_escaped(self):
        smuggled = "".join(chr(0xE0000 + ord(c)) for c in "PI")
        self.assertIn("\\U000e0050", score.visible(smuggled))

    def test_newlines_escaped_so_report_stays_one_line(self):
        self.assertNotIn("\n", score.visible("line1\nline2"))

    def test_truncated(self):
        self.assertTrue(score.visible("a" * 1000).endswith("..."))


class LoadCasesTests(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_ok(self):
        p = self._write('{"id":"a","entry_point":"chat","payload":"hi"}\n')
        self.assertEqual(len(run_harness.load_cases(p)), 1)

    def test_missing_keys(self):
        p = self._write('{"entry_point":"chat"}\n')
        with self.assertRaises(SystemExit):
            run_harness.load_cases(p)

    def test_bad_json(self):
        p = self._write("not json\n")
        with self.assertRaises(SystemExit):
            run_harness.load_cases(p)


class EchoHandler(BaseHTTPRequestHandler):
    status = 200

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        out = body.encode()
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def http_args(url, **kw):
    base = dict(url=url, insecure=False, header=[], bearer=None, field="message",
                bare=False, method="POST", timeout=5, max_bytes=10_000)
    base.update(kw)
    return argparse.Namespace(**base)


class HttpTargetTests(unittest.TestCase):
    def test_echo_and_status(self):
        srv, url = serve(EchoHandler)
        self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        out = run_harness.make_http_target(http_args(url))("chat", "hello")
        self.assertEqual(out["status"], 200)
        self.assertIn("hello", out["answer"])

    def test_5xx_is_result_not_error(self):
        class Boom(EchoHandler):
            status = 500
        srv, url = serve(Boom)
        self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        out = run_harness.make_http_target(http_args(url))("chat", "x")
        self.assertEqual(out["status"], 500)

    def test_bearer_header(self):
        seen = {}

        class Cap(EchoHandler):
            def do_POST(self):
                seen["auth"] = self.headers.get("Authorization")
                super().do_POST()
        srv, url = serve(Cap)
        self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        run_harness.make_http_target(http_args(url, bearer="tok"))("chat", "x")
        self.assertEqual(seen["auth"], "Bearer tok")

    def test_max_bytes_cap(self):
        srv, url = serve(EchoHandler)
        self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        out = run_harness.make_http_target(http_args(url, max_bytes=5))("chat", "0123456789")
        self.assertLessEqual(len(out["answer"]), 5)

    def test_rejects_non_http(self):
        with self.assertRaises(SystemExit):
            run_harness.make_http_target(http_args("ftp://x/"))

    def test_rejects_empty_host(self):
        with self.assertRaises(SystemExit):
            run_harness.make_http_target(http_args("http:///nohost"))

    def test_shape_response_plain_text(self):
        out = run_harness.shape_response("hello there", 200)
        self.assertEqual(out["answer"], "hello there")
        self.assertEqual(out["requests"], [])

    def test_shape_response_lifts_agent_telemetry(self):
        body = json.dumps({"answer": "ok", "requests": ["http://h/PI-x"],
                           "tool_calls": [{"name": "ping", "args": {"h": "PI-x"}}]})
        out = run_harness.shape_response(body, 200)
        self.assertEqual(out["answer"], "ok")
        self.assertEqual(out["requests"], ["http://h/PI-x"])
        self.assertEqual(out["tool_calls"][0]["name"], "ping")

    def test_shape_response_json_without_known_keys(self):
        out = run_harness.shape_response('{"message": "hi"}', 200)
        self.assertIn("message", out["answer"])

    def test_crash_is_error_record(self):
        def boom(ep, p):
            raise RuntimeError("nope")
        r = run_harness.run_case(boom, {"id": "z", "entry_point": "c", "payload": "p"})
        self.assertFalse(r["ok"])


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        listener.RECORD = os.path.join(self.dir, "hits.log")
        listener.QUIET = True
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), listener.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_serves_png_and_logs_canary(self):
        with urllib.request.urlopen(f"{self.url}/PI-deadbeef.png") as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.read().startswith(b"\x89PNG"))
        with open(listener.RECORD, encoding="utf-8") as fh:
            self.assertIn("PI-deadbeef", fh.read())


if __name__ == "__main__":
    unittest.main()
