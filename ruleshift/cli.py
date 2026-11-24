import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from .cases import generate_cases
from .evaluation import evaluate, summarize
from .providers import PROVIDERS, Provider, ProviderError, parse_answer
from .storage import Store
from .comparison import compare


def provider_arguments(parser):
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env", help="Environment variable name, never the secret value")
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--omit-temperature", action="store_true", help="Explicitly omit unsupported sampling parameter; recorded in protocol")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--timeout", type=float, default=60)


def configured_provider(args):
    provider = Provider(args.provider,
                        args.model or (args.provider if args.provider.startswith("demo-") else ""),
                        args.base_url or "", args.api_key_env, args.timeout,
                        None if args.omit_temperature else args.temperature, args.max_tokens)
    if not provider.simulated:
        provider.request("Preflight request validation; no network call")
    return provider


def export_run(store, run_id, directory):
    dest = Path(directory) / run_id
    dest.mkdir(parents=True, exist_ok=True)
    run, rows = store.get_run(run_id), store.results(run_id)
    summary = summarize(rows)
    (dest / "report.json").write_text(json.dumps({"run": run, "summary": summary, "results": rows}, indent=2))
    if len(rows) == run["config"]["cases"] * run["config"]["repeats"]:
        (dest / "publish.sql").write_text(store.publish_sql(run_id) + "\n")
    with (dest / "cases.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        for row in rows:
            # Safe to open in a spreadsheet; preserve exact strings in report.json.
            safe = {k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v)
                    for k, v in row.items()}
            writer.writerow(safe)
    label = "SIMULATED CONTROL — not a model evaluation" if run["simulated"] else "REAL MODEL EVALUATION"
    lines = [f"# RuleShift — {run['model']}", "", label, "", summary["interpretation"], "",
             f"Run: `{run_id}`  Dataset SHA-256: `{run['dataset_hash']}`", "",
             "| Variation | Correct / attempts | E2E accuracy | Valid responses |", "|---|---:|---:|---:|"]
    for name, result in summary["variants"].items():
        lines.append(f"| {name} | {result['correct']}/{result['attempts']} | {result['accuracy_e2e']:.1%} | {result['valid']} |")
    lines += ["", "E2E accuracy counts parsing/provider errors as failures. JSON includes accuracy on valid answers separately.",
              "95% intervals in JSON resample template+seed clusters; they do not establish general performance across domains.",
              "", "Inspect cases.csv and report.json for original prompts, answer keys, responses, statuses and timings."]
    lines += ["", "Grafana: start `docker compose up -d`, then run `python3 -m ruleshift publish " + run_id + "`.",
              "Open http://localhost:3018/d/ruleshift/ruleshift and select this experiment."]
    (dest / "README.md").write_text("\n".join(lines) + "\n")
    return dest, summary


def serve(database, host, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            store = Store(database)
            try:
                if path == "/health":
                    payload = {"status": "ok", "service": "ruleshift"}
                elif path == "/runs":
                    payload = store.list_runs()
                elif path.startswith("/runs/"):
                    run_id = path.split("/")[-1]
                    payload = {"run": store.get_run(run_id), "summary": summarize(store.results(run_id)),
                               "results": store.results(run_id)}
                else:
                    self.send_error(404)
                    return
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except ValueError:
                self.send_error(404)
            finally:
                store.close()
    print(f"Read-only results API: http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main(argv=None):
    parser = argparse.ArgumentParser(description="RuleShift — controlled generalization tests")
    parser.add_argument("--database", default=os.getenv("RULESHIFT_DB", "data/ruleshift.sqlite"))
    subs = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "demo"):
        p = subs.add_parser(command)
        p.add_argument("--seeds", type=int, default=10)
        p.add_argument("--start-seed", type=int, default=1000)
        p.add_argument("--repeats", type=int, default=1)
        p.add_argument("--suite", choices=("all", "math", "policy"), default="all")
        p.add_argument("--reports", default=os.getenv("RULESHIFT_REPORTS", "reports"))
        p.add_argument("--publish", action="store_true", help="Mirror the completed run to local PostgreSQL for Grafana")
        if command == "run":
            provider_arguments(p)
    check = subs.add_parser("check", help="One task to test endpoint connectivity, output format and answer correctness separately")
    provider_arguments(check)
    listing = subs.add_parser("list")
    listing.add_argument("--json", action="store_true", help="Include full saved run metadata")
    publish = subs.add_parser("publish", help="Mirror a completed experiment to PostgreSQL for the local Grafana dashboard")
    publish.add_argument("run_id")
    publish.add_argument("--reports", default=os.getenv("RULESHIFT_REPORTS", "reports"))
    comparison = subs.add_parser("compare", help="Compare complete runs with identical datasets and requested settings")
    comparison.add_argument("run_a")
    comparison.add_argument("run_b")
    comparison.add_argument("--output")
    export = subs.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("--reports", default="reports")
    server = subs.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=9138)
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.database, args.host, args.port)
        return
    store = Store(args.database)
    try:
        if args.command == "list":
            runs = store.list_runs()
            if args.json:
                print(json.dumps(runs, indent=2))
            elif not runs:
                print("No experiments yet. Start with: python3 -m ruleshift demo --seeds 3 --repeats 2")
            else:
                print(f"{'RUN ID':32}  {'EVIDENCE':9}  {'PROVIDER':13}  MODEL")
                for run in runs:
                    evidence = "simulated" if run["simulated"] else "real"
                    print(f"{run['run_id']}  {evidence:9}  {run['provider']:13}  {run['model']}")
            return
        if args.command == "publish":
            export_run(store, args.run_id, args.reports)
            count = store.publish(os.getenv("RULESHIFT_POSTGRES_DSN"), args.run_id)
            print(f"Published {count} results to PostgreSQL. Grafana: http://localhost:3018/d/ruleshift/ruleshift")
            return
        if args.command == "compare":
            result = compare(store, args.run_a, args.run_b)
            text = json.dumps(result, indent=2)
            if args.output:
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(text + "\n")
            print(text)
            return
        if args.command == "check":
            provider = configured_provider(args)
            case = generate_cases(1, families=("math",))[0]
            print("Preflight: exactly one task; a hosted provider may charge for this request.")
            try:
                response = provider.call(case.prompt)
            except Exception as error:
                status = getattr(error, "http_status", None)
                print(json.dumps({"endpoint_reachable": status is not None, "request_succeeded": False,
                                  "http_status": status, "response_format_valid": False,
                                  "answer_correct": None, "error_type": type(error).__name__,
                                  "message": str(error) if isinstance(error, ProviderError) else "Provider request failed; check endpoint and API contract"}))
                raise SystemExit(2)
            try:
                observed = parse_answer(response.raw, case.expected)
            except (ValueError, TypeError, OverflowError):
                print(json.dumps({"endpoint_reachable": True, "request_succeeded": True,
                                  "response_format_valid": False, "answer_correct": None}))
                raise SystemExit(2)
            correct = observed == case.expected
            print(json.dumps({"endpoint_reachable": True, "request_succeeded": True, "response_format_valid": True,
                              "answer_correct": correct, "provider": provider.kind, "model": provider.model,
                              "note": "One successful probe does not certify overall model quality."}, indent=2))
            if not correct:
                raise SystemExit(1)
            return
        if args.command == "export":
            dest, _ = export_run(store, args.run_id, args.reports)
            print(dest)
            return
        families = ("math", "policy") if args.suite == "all" else (args.suite,)
        cases = generate_cases(args.seeds, args.start_seed, families)
        providers = ([Provider("demo-oracle", "SIMULATED / rule parser"),
                      Provider("demo-shortcut", "SIMULATED / brittle shortcut")]
                     if args.command == "demo" else [configured_provider(args)])
        failed = False
        for provider in providers:
            print(f"Evaluating {provider.model}: {len(cases) * args.repeats} calls", flush=True)
            def progress(done, total):
                if done % 20 == 0 or done == total:
                    print(f"  {done}/{total}", flush=True)
            run_id = evaluate(store, cases, provider, args.repeats, progress)
            dest, summary = export_run(store, run_id, args.reports)
            print(f"Run {run_id}: {summary['correct']}/{summary['attempts']} correct; "
                  f"{summary['provider_errors']} provider errors; {summary['parse_errors']} parse errors")
            print(f"Report: {dest}")
            if args.publish:
                count = store.publish(os.getenv("RULESHIFT_POSTGRES_DSN"), run_id)
                print(f"Published {count} results to PostgreSQL. Grafana: http://localhost:3018/d/ruleshift/ruleshift")
            failed |= summary["valid"] == 0
        if failed:
            raise SystemExit(2)
    except (ValueError, RuntimeError, KeyError) as error:
        # Do not print DSNs or environment values (could contain secrets).
        print(f"Error: {error if not isinstance(error, KeyError) else 'required environment variable is missing'}", file=sys.stderr)
        raise SystemExit(2)
    finally:
        store.close()
