import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ruleshift import datadog
from ruleshift.cases import generate_cases
from ruleshift.cli import main
from ruleshift.comparison import compare
from ruleshift.evaluation import evaluate
from ruleshift.providers import Provider, ProviderError, parse_answer
from ruleshift.storage import Store


class HTTPFixture(unittest.TestCase):
    def setUp(self):
        self.requests = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body, dict(self.headers)))
                if self.path == "/v1/messages":
                    payload = {"content": [{"type": "thinking", "thinking": "not the final answer"},
                                           {"type": "text", "text": '{"answer":5}'}],
                               "usage": {"input_tokens": 11, "output_tokens": 4}}
                elif self.path == "/v1/responses":
                    payload = {"output": [{"type": "reasoning", "summary": []},
                                          {"type": "message", "content": [{"type": "output_text", "text": '{"answer":5}'}]}],
                               "usage": {"input_tokens": 11, "output_tokens": 4}}
                elif self.path.startswith("/v1beta/models/"):
                    payload = {"candidates": [{"content": {"parts": [{"text": "private thought", "thought": True},
                                                                      {"text": '{"answer":5}'}]}}],
                               "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4}}
                elif self.path == "/api/v1/dashboard":
                    payload = {"id": "fixture-board", "url": "/dashboard/fixture-board"}
                else:
                    payload = {}
                data = json.dumps(payload).encode()
                self.send_response(202 if self.path.startswith("/api/v2/") else 200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class NativeProviderTests(HTTPFixture):
    def test_anthropic_header_and_system_message(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-claude-key"}):
            p = Provider("anthropic", "test-claude", self.url + "/v1")
            response = p.call("2 plus 3?")
        path, body, headers = self.requests[0]
        self.assertEqual(path, "/v1/messages")
        self.assertEqual(headers["X-Api-Key"], "test-claude-key")
        self.assertEqual(headers["Anthropic-Version"], "2023-06-01")
        self.assertIn("system", body)
        self.assertEqual([m["role"] for m in body["messages"]], ["user"])
        self.assertEqual(parse_answer(response.raw, 5), 5)
        self.assertEqual((response.input_tokens, response.output_tokens), (11, 4))
        self.assertNotIn("test-claude-key", json.dumps(p.config()))

    def test_responses_final_text_and_explicit_temperature_omission(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-responses-key"}):
            p = Provider("responses", "test-coding-model", self.url + "/v1", temperature=None)
            response = p.call("2 plus 3?")
        path, body, headers = self.requests[0]
        self.assertEqual(path, "/v1/responses")
        self.assertNotIn("temperature", body)
        self.assertIsNone(p.config()["temperature"])
        self.assertEqual(body["max_output_tokens"], 512)
        self.assertFalse(body["store"])
        self.assertEqual(headers["Authorization"], "Bearer test-responses-key")
        self.assertEqual(parse_answer(response.raw, 5), 5)
        self.assertEqual((response.input_tokens, response.output_tokens), (11, 4))

    def test_gemini_header_not_url_and_thoughts_ignored(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}):
            p = Provider("gemini", "models/test-model", self.url + "/v1beta")
            response = p.call("2 plus 3?")
        path, body, headers = self.requests[0]
        self.assertEqual(path, "/v1beta/models/test-model:generateContent")
        self.assertNotIn("test-gemini-key", path)
        self.assertEqual(headers["X-Goog-Api-Key"], "test-gemini-key")
        self.assertIn("systemInstruction", body)
        self.assertEqual(parse_answer(response.raw, 5), 5)
        self.assertEqual((response.input_tokens, response.output_tokens), (11, 4))

    def test_missing_credentials_do_not_make_a_request(self):
        with patch.dict(os.environ, {}, clear=True):
            for kind in ("responses", "anthropic", "gemini"):
                with self.assertRaisesRegex(ValueError, "Set"):
                    Provider(kind, "test", self.url).call("hello")
        self.assertEqual(self.requests, [])


class DatadogTests(HTTPFixture):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.directory.name) / "test.sqlite"))
        self.cases = generate_cases(2)
        self.run = evaluate(self.store, self.cases, Provider("demo-shortcut", "control"))
        self.urls = {"site": "datadoghq.com", "logs": self.url + "/api/v2/logs",
                     "metrics": self.url + "/api/v2/series", "dashboard": self.url + "/api/v1/dashboard"}

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()
        super().tearDown()

    def test_default_payload_omits_content_and_high_cardinality_metric_tags(self):
        data = datadog.payloads(self.store, self.run, now=1700000000)
        self.assertEqual(len(data["logs"]), len(self.cases) + 1)
        for event in data["logs"]:
            self.assertNotIn("prompt", event)
            self.assertNotIn("raw_response", event)
            self.assertEqual(event["evidence"], "simulated")
        for series in data["metrics"]["series"]:
            self.assertEqual(series["type"], 3)
            self.assertFalse(any(t.startswith(("run_id:", "case_id:", "dataset_hash:")) for t in series["tags"]))

    def test_content_is_explicit_opt_in(self):
        data = datadog.payloads(self.store, self.run, include_content=True)
        attempts = [event for event in data["logs"] if event["event_type"] == "attempt"]
        self.assertTrue(all("prompt" in event and "raw_response" in event for event in attempts))

    def test_pair_attributes_match_local_summary(self):
        data = datadog.payloads(self.store, self.run)
        attempts = [r for r in data["logs"] if r["event_type"] == "attempt"]
        self.assertEqual(sum(r["paired_regression"] for r in attempts),
                         sum(v["baseline_correct_variant_wrong"] for v in data["summary"]["paired"].values()))
        self.assertEqual(sum(r["invariance_violation"] for r in attempts),
                         sum(v["invariance_violations"] for v in data["summary"]["paired"].values()))

    def test_batches_respect_utf8_byte_size_and_event_limit(self):
        events = [{"message": "é" * 100} for _ in range(5)]
        batches = list(datadog.batches(events, max_bytes=1500, max_events=2))
        self.assertEqual([len(b) for b in batches], [2, 2, 1])
        self.assertTrue(all(len(json.dumps(b).encode()) <= 1500 for b in batches))

    def test_offline_export_needs_no_key_and_sends_nothing(self):
        with patch.dict(os.environ, {}, clear=True):
            directory, data = datadog.export_payloads(self.store, self.run, self.directory.name)
        self.assertFalse(json.loads((directory / "manifest.json").read_text())["sent"])
        self.assertEqual(self.requests, [])
        self.assertEqual(json.loads((directory / "logs.json").read_text()), data["logs"])

    def test_publish_contract_receipts_and_optional_metrics(self):
        with patch.dict(os.environ, {"DD_API_KEY": "test-dd-key"}), patch("ruleshift.datadog.endpoints", return_value=self.urls):
            first = datadog.publish(self.store, self.run, self.directory.name, metrics=True)
            second = datadog.publish(self.store, self.run, self.directory.name, metrics=True)
        self.assertEqual(first["accepted_requests"], 2)
        self.assertEqual(second["accepted_requests"], 0)
        self.assertEqual(second["skipped_requests"], 2)
        self.assertEqual([r[0] for r in self.requests], ["/api/v2/logs", "/api/v2/series"])
        self.assertTrue(all(r[2]["Dd-Api-Key"] == "test-dd-key" for r in self.requests))
        receipt = Path(self.directory.name) / self.run / "datadog/receipts.json"
        self.assertNotIn("test-dd-key", receipt.read_text())

    def test_changed_content_mode_cannot_double_count_run(self):
        with patch.dict(os.environ, {"DD_API_KEY": "test-dd-key"}), patch("ruleshift.datadog.endpoints", return_value=self.urls):
            datadog.publish(self.store, self.run, self.directory.name)
            with self.assertRaisesRegex(ValueError, "different content mode"):
                datadog.publish(self.store, self.run, self.directory.name, include_content=True)
        self.assertEqual(len(self.requests), 1)

    def test_unacknowledged_batch_is_not_recorded(self):
        with patch.dict(os.environ, {"DD_API_KEY": "test-dd-key"}), patch("ruleshift.datadog.post_json", side_effect=RuntimeError("HTTP 403")):
            with self.assertRaises(RuntimeError):
                datadog.publish(self.store, self.run, self.directory.name)
        self.assertFalse((Path(self.directory.name) / self.run / "datadog/receipts.json").exists())

    def test_keys_and_site_validation(self):
        for site in ("https://datadoghq.com", "evil.example", "app.datadoghq.com"):
            with self.assertRaises(ValueError):
                datadog.endpoints(site)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "DD_API_KEY"):
                datadog.publish(self.store, self.run, self.directory.name)
            with self.assertRaisesRegex(ValueError, "DD_APP_KEY"):
                datadog.create_dashboard(datadog.dashboard())
        self.assertEqual(datadog.endpoints("ap2.datadoghq.com")["metrics"], "https://api.ap2.datadoghq.com/api/v2/series")

    def test_dashboard_generation_and_creation_are_separate(self):
        payload = datadog.dashboard(self.run)
        self.assertEqual(payload["template_variables"][0]["default"], self.run)
        self.assertEqual(self.requests, [])
        with patch.dict(os.environ, {"DD_API_KEY": "test-dd-key", "DD_APP_KEY": "test-app-key"}), patch("ruleshift.datadog.endpoints", return_value=self.urls):
            result = datadog.create_dashboard(payload)
        self.assertEqual(result["id"], "fixture-board")
        self.assertEqual(self.requests[0][0], "/api/v1/dashboard")


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.directory.name) / "test.sqlite"))
        cases = generate_cases(1)
        self.a = evaluate(self.store, cases, Provider("demo-oracle", "reference"))
        self.b = evaluate(self.store, cases, Provider("demo-shortcut", "shortcut"))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_matched_comparison_and_negative_delta(self):
        result = compare(self.store, self.a, self.b)
        self.assertEqual(result["variants"]["names"]["delta_percentage_points_b_minus_a"], 0)
        self.assertLess(result["variants"]["irrelevant"]["delta_percentage_points_b_minus_a"], 0)

    def test_unmatched_data_and_protocol_rejected(self):
        different = evaluate(self.store, generate_cases(1, 2000), Provider("demo-oracle", "other"))
        with self.assertRaisesRegex(ValueError, "Dataset mismatch"):
            compare(self.store, self.a, different)
        other = evaluate(self.store, generate_cases(1), Provider("demo-oracle", "other", temperature=None))
        with self.assertRaisesRegex(ValueError, "temperature"):
            compare(self.store, self.a, other)

    def test_mixed_evidence_rejected(self):
        self.store.db.execute("UPDATE runs SET simulated=0 WHERE run_id=?", (self.b,))
        self.store.db.commit()
        with self.assertRaisesRegex(ValueError, "simulated"):
            compare(self.store, self.a, self.b)

    def test_cli_missing_key_stops_before_model_evaluation(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main(["--database", str(Path(self.directory.name) / "empty.sqlite"), "run", "--provider", "anthropic", "--model", "test"])
        self.assertEqual(error.exception.code, 2)
        empty = Store(str(Path(self.directory.name) / "empty.sqlite"))
        self.assertEqual(empty.list_runs(), [])
        empty.close()

    def test_check_distinguishes_http_rejection_from_unreachable_host(self):
        output = io.StringIO()
        with patch("ruleshift.providers.Provider.call", side_effect=ProviderError("provider HTTP 401", 401)), patch("sys.stdout", new=output):
            with self.assertRaises(SystemExit):
                main(["--database", str(Path(self.directory.name) / "check.sqlite"), "check", "--provider", "ollama", "--model", "fixture"])
        diagnostic = json.loads(output.getvalue().split("\n", 1)[1])
        self.assertTrue(diagnostic["endpoint_reachable"])
        self.assertFalse(diagnostic["request_succeeded"])
        self.assertEqual(diagnostic["http_status"], 401)
