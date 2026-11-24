import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ruleshift.cases import Case, dataset_hash, generate_cases
from ruleshift.cli import export_run, main
from ruleshift.evaluation import cluster_interval, evaluate, summarize
from ruleshift.providers import Provider, Response, parse_answer
from ruleshift.storage import Store


class CaseTests(unittest.TestCase):
    def test_reproducibility_and_dataset_identity(self):
        a, b = generate_cases(5), generate_cases(5)
        self.assertEqual(a, b)
        self.assertEqual(dataset_hash(a), dataset_hash(b))
        self.assertNotEqual(dataset_hash(a), dataset_hash(generate_cases(5, 2000)))
        self.assertEqual(len({c.case_id for c in a}), len(a))

    def test_selection_does_not_change_cases(self):
        all_cases = generate_cases(5)
        self.assertEqual([c for c in all_cases if c.family == "math"],
                         generate_cases(5, families=("math",)))

    def test_invariants_and_nonnegative_math(self):
        cases = generate_cases(100)
        bases = {(c.template_id, c.seed): c for c in cases if c.variant == "baseline"}
        for case in cases:
            if case.relation == "invariant":
                base = bases[(case.template_id, case.seed)]
                self.assertEqual(case.expected, base.expected)
                self.assertNotEqual(case.prompt, base.prompt)
            if case.family == "math":
                self.assertIs(type(case.expected), int)
                self.assertGreaterEqual(case.expected, 0)

    def test_policy_cases_are_not_all_positive(self):
        cases = generate_cases(100, families=("policy",))
        baseline = [c.expected for c in cases if c.variant == "baseline"]
        self.assertIn(True, baseline)
        self.assertIn(False, baseline)

    def test_refund_boundary_inclusive_and_next_day(self):
        for case in generate_cases(50, families=("policy",)):
            if case.variant == "boundary":
                self.assertIn(f"returned on day {30 + case.seed % 2}.", case.prompt)
                self.assertEqual(case.expected, case.seed % 2 == 0 and "item is unused," in case.prompt)

    def test_independent_scripted_parser_agrees_with_all_oracles(self):
        # A separate text parser cross-checks generation; not an LLM benchmark.
        provider = Provider("demo-oracle", "control")
        for case in generate_cases(50):
            self.assertEqual(parse_answer(provider.call(case.prompt).raw, case.expected), case.expected)

    def test_invalid_generation(self):
        for options in ({"seeds": 0}, {"seeds": 10001}, {"start_seed": -1}, {"families": ("typo",)}):
            with self.assertRaises(ValueError):
                generate_cases(**options)


class ParsingTests(unittest.TestCase):
    def test_strict_numeric_types(self):
        self.assertEqual(parse_answer('{"answer": 42.0}', 42), 42)
        for raw in ('{"answer": true}', '{"answer": "42"}', '{"answer": NaN}',
                    '{"answer": Infinity}', '[42]', '42', '{"answer":42,"reason":"x"}'):
            with self.assertRaises((ValueError, TypeError)):
                parse_answer(raw, 42)

    def test_strict_boolean(self):
        self.assertIs(parse_answer('{"answer": false}', False), False)
        for raw in ('{"answer": 0}', '{"answer": "false"}', '{"answer":null}'):
            with self.assertRaises(ValueError):
                parse_answer(raw, False)

    def test_json_fence_supported_without_last_number_guessing(self):
        self.assertEqual(parse_answer('```json\n{"answer": 5}\n```', 5), 5)
        with self.assertRaises(ValueError):
            parse_answer('I think it is 4, or perhaps 5', 5)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.directory.name) / "test.sqlite"))

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_offline_controls_and_paired_failures(self):
        cases = generate_cases(5)
        good = evaluate(self.store, cases, Provider("demo-oracle", "control"), repeats=2)
        bad = evaluate(self.store, cases, Provider("demo-shortcut", "shortcut"), repeats=2)
        good_summary = summarize(self.store.results(good))
        bad_summary = summarize(self.store.results(bad))
        self.assertEqual(good_summary["correct"], len(cases) * 2)
        self.assertGreater(bad_summary["paired"]["irrelevant"]["invariance_violations"], 0)
        self.assertGreater(bad_summary["paired"]["rule"]["baseline_correct_variant_wrong"], 0)
        self.assertEqual(self.store.get_run(good)["dataset_hash"], self.store.get_run(bad)["dataset_hash"])

    def test_error_denominators_and_no_oracle_leak(self):
        class MixedProvider:
            model, kind, simulated = "broken", "test", False
            count = 0
            def config(self):
                return {}
            def call(self, prompt):
                self.count += 1
                self_outer.assertIsInstance(prompt, str)
                self_outer.assertNotIn('"expected"', prompt)
                if self.count == 1:
                    raise TimeoutError("do not persist this secret")
                if self.count == 2:
                    return Response("not JSON")
                return Response('{"answer":999999}')
        self_outer = self
        run = evaluate(self.store, generate_cases(1, families=("math",)), MixedProvider())
        rows = self.store.results(run)
        summary = summarize(rows)
        self.assertEqual(summary["provider_errors"], 1)
        self.assertEqual(summary["parse_errors"], 1)
        self.assertEqual(summary["valid"], len(rows) - 2)
        self.assertEqual(summary["correct"], 0)
        self.assertNotIn("do not persist", json.dumps(rows))

    def test_repeats_are_not_independent_ci_clusters(self):
        rows = [{"template_id": "one", "seed": 1, "correct": value} for value in [1, 0, 1, 0]]
        self.assertIsNone(cluster_interval(rows))
        rows.append({"template_id": "one", "seed": 2, "correct": 1})
        self.assertEqual(cluster_interval(rows), cluster_interval(rows))

    def test_export_contains_provenance_and_csv(self):
        run = evaluate(self.store, generate_cases(1), Provider("demo-oracle", "control"))
        dest, _ = export_run(self.store, run, self.directory.name)
        self.assertIn("SIMULATED CONTROL", (dest / "README.md").read_text())
        exported = json.loads((dest / "report.json").read_text())
        self.assertEqual(len(exported["results"]), 18)
        self.assertTrue((dest / "cases.csv").read_text().startswith("run_id,"))

    def test_unknown_run_and_repeats_rejected(self):
        with self.assertRaises(ValueError):
            self.store.get_run("missing")
        with self.assertRaises(ValueError):
            evaluate(self.store, generate_cases(1), Provider("demo-oracle", "control"), repeats=0)

    def test_sql_export_and_incomplete_run_guard(self):
        model = "O'Brien'); DROP TABLE runs; --"
        run = evaluate(self.store, generate_cases(1), Provider("demo-oracle", model))
        sql = self.store.publish_sql(run)
        self.assertIn("O''Brien''); DROP TABLE runs; --", sql)
        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertTrue(sql.endswith("COMMIT;"))
        self.store.db.execute("DELETE FROM results WHERE run_id=? AND case_id=?", (run, "inventory-1000-baseline"))
        self.store.db.commit()
        with self.assertRaisesRegex(ValueError, "Incomplete run"):
            self.store.publish_sql(run)

    def test_baseline_repeat_variability_is_separate(self):
        rows = [{"template_id": "test", "seed": 1, "variant": "baseline", "status": "ok",
                 "observed": str(value), "correct": int(value == 1), "repeat_index": repeat}
                for repeat, value in enumerate((1, 2))]
        summary = summarize(rows)
        self.assertEqual(summary["baseline_repeat_variability"]["rate"], 1)
        self.assertEqual(summary["paired"], {})

    def test_numeric_formatting_is_not_semantic_repeat_variability(self):
        rows = [{"template_id": "test", "seed": 1, "variant": "baseline", "status": "ok",
                 "observed": value, "correct": 1, "repeat_index": repeat}
                for repeat, value in enumerate(("5", "5.0"))]
        self.assertEqual(summarize(rows)["baseline_repeat_variability"]["rate"], 0)

    def test_default_demo_publish_uses_postgres_without_datadog_credentials(self):
        with patch.dict("os.environ", {}, clear=True), patch("ruleshift.storage.Store.publish", return_value=18) as publish, \
                patch("ruleshift.datadog.publish", side_effect=AssertionError("Datadog must not be called")), \
                patch("sys.stdout", new=io.StringIO()):
            main(["--database", str(Path(self.directory.name) / "cli.sqlite"), "demo", "--seeds", "1", "--publish",
                  "--reports", str(Path(self.directory.name) / "reports")])
        self.assertEqual(publish.call_count, 2)
        self.assertTrue(all(call.args[0] is None for call in publish.call_args_list))
        exports = list((Path(self.directory.name) / "reports").glob("*/publish.sql"))
        self.assertEqual(len(exports), 2)
        self.assertFalse(list((Path(self.directory.name) / "reports").glob("*/datadog")))

    def test_active_cli_has_no_datadog_commands(self):
        output = io.StringIO()
        with patch("sys.stdout", new=output), self.assertRaises(SystemExit) as error:
            main(["--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertNotIn("datadog", output.getvalue().lower())

    def test_cli_failure_returns_nonzero(self):
        with patch("ruleshift.providers.Provider.call", side_effect=TimeoutError), patch("sys.stdout", new=io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main(["--database", str(Path(self.directory.name) / "cli.sqlite"), "run",
                      "--provider", "ollama", "--model", "test", "--seeds", "1",
                      "--reports", str(Path(self.directory.name) / "reports")])
        self.assertEqual(error.exception.code, 2)


class ProviderHTTPTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body, self.headers.get("Authorization")))
                if body["model"] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://localhost:9/never-forward")
                    self.end_headers()
                    return
                payload = ({"message": {"content": '{"answer":5}'}, "prompt_eval_count": 12, "eval_count": 4}
                           if self.path == "/api/chat" else
                           {"choices": [{"message": {"content": '{"answer":5}'}}],
                            "usage": {"prompt_tokens": 12, "completion_tokens": 4}})
                data = json.dumps(payload).encode()
                self.send_response(200)
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

    def test_ollama_request_contract_and_usage(self):
        response = Provider("ollama", "local-test", self.url).call("2 plus 3?")
        path, body, auth = self.requests[0]
        self.assertEqual(path, "/api/chat")
        self.assertFalse(body["stream"])
        self.assertEqual(body["messages"][-1], {"role": "user", "content": "2 plus 3?"})
        self.assertIsNone(auth)
        self.assertEqual((response.input_tokens, response.output_tokens), (12, 4))

    def test_compatible_endpoint_auth_not_in_config(self):
        with patch.dict("os.environ", {"TEST_RULESHIFT_KEY": "local-test-secret"}):
            provider = Provider("chat", "local-test", self.url + "/v1", "TEST_RULESHIFT_KEY")
            provider.call("hello")
        path, _, auth = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(auth, "Bearer local-test-secret")
        self.assertNotIn("local-test-secret", json.dumps(provider.config()))

    def test_redirect_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
            Provider("chat", "redirect", self.url).call("hello")
        self.assertEqual(len(self.requests), 1)

    def test_bad_urls_and_model_names_rejected(self):
        for url in ("file:///tmp/model", "https://user:pass@example.org", "https://example.org?key=secret"):
            with self.assertRaises(ValueError):
                Provider("chat", "test", url)
        with self.assertRaises(ValueError):
            Provider("chat", "", self.url)


if __name__ == "__main__":
    unittest.main()
