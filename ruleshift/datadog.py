"""Datadog HTTP intake: inspectable payloads, explicit publishing, no agent required.

Experiment IDs stay in log attributes, not custom-metric tags. Numeric log
attributes must be configured as measures for the analytics dashboard.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from .evaluation import summarize

SITES = ("datadoghq.com", "us3.datadoghq.com", "us5.datadoghq.com", "datadoghq.eu",
         "ap1.datadoghq.com", "ap2.datadoghq.com", "uk1.datadoghq.com", "ddog-gov.com", "us2.ddog-gov.com")


def endpoints(site=None):
    site = site or os.getenv("DD_SITE", "datadoghq.com")
    if site not in SITES:
        raise ValueError("Unsupported DD_SITE; use a documented Datadog site hostname, without https:// or app.")
    return {"site": site, "logs": f"https://http-intake.logs.{site}/api/v2/logs",
            "metrics": f"https://api.{site}/api/v2/series",
            "dashboard": f"https://api.{site}/api/v1/dashboard"}


def tag(value):
    return re.sub(r"[^a-zA-Z0-9_.:/-]", "_", str(value))[:160]


def payloads(store, run_id, include_content=False, now=None):
    run, rows = store.get_run(run_id), store.results(run_id)
    if len(rows) != run["config"]["cases"] * run["config"]["repeats"]:
        raise ValueError("Incomplete run: finish evaluation before publishing")
    now = int(time.time() if now is None else now)
    evidence = "simulated" if run["simulated"] else "real"
    base_tags = ["service:ruleshift", f"env:{tag(os.getenv('DD_ENV', 'local'))}",
                 f"evidence:{evidence}", f"provider:{tag(run['provider'])}", f"model:{tag(run['model'])}"]
    baseline = {(r["template_id"], r["seed"], r["repeat_index"]): r for r in rows if r["variant"] == "baseline"}
    logs = []
    for row in rows:
        base = baseline.get((row["template_id"], row["seed"], row["repeat_index"]))
        valid_pair = row["variant"] != "baseline" and base is not None and base["status"] == "ok" and row["status"] == "ok"
        invariant_pair = valid_pair and row["relation"] == "invariant"
        changed = invariant_pair and json.loads(base["observed"]) != json.loads(row["observed"])
        log = {"service": "ruleshift", "ddsource": "ruleshift", "ddtags": ",".join(base_tags),
               "timestamp": now * 1000, "evaluated_at": run["created_at"], "event_type": "attempt",
               "message": f"{evidence.upper()} | {row['case_id']} | {row['status']} | correct={row['correct']}",
               "event_id": hashlib.sha256(f"{run_id}:{row['case_id']}:{row['repeat_index']}".encode()).hexdigest(),
               "run_id": run_id, "dataset_hash": run["dataset_hash"], "evidence": evidence,
               "model": run["model"], "provider": run["provider"], "case_id": row["case_id"],
               "family": row["family"], "template_id": row["template_id"], "seed": row["seed"],
               "variant": row["variant"], "repeat_index": row["repeat_index"], "relation": row["relation"],
               "status": row["status"], "correct": row["correct"], "valid": int(row["status"] == "ok"),
               "expected": json.loads(row["expected"]),
               "observed": json.loads(row["observed"]) if row["observed"] is not None else None,
               "latency_ms": row["latency_ms"], "valid_pair": int(valid_pair),
               "baseline_correct_pair": int(valid_pair and base["correct"] == 1),
               "paired_regression": int(valid_pair and base["correct"] == 1 and row["correct"] == 0),
               "invariant_pair": int(invariant_pair), "invariance_violation": int(changed)}
        if row["input_tokens"] is not None:
            log["input_tokens"] = row["input_tokens"]
        if row["output_tokens"] is not None:
            log["output_tokens"] = row["output_tokens"]
        if include_content:
            log.update({"prompt": row["prompt"][:16384], "raw_response": row["raw_response"][:16384],
                        "baseline_prompt": base["prompt"][:16384] if base else None})
        logs.append(log)
    summary = summarize(rows)
    logs.append({"service": "ruleshift", "ddsource": "ruleshift", "ddtags": ",".join(base_tags),
                 "timestamp": now * 1000, "event_type": "run_summary", "run_id": run_id,
                 "event_id": f"{run_id}:summary", "model": run["model"], "provider": run["provider"],
                 "evidence": evidence, "dataset_hash": run["dataset_hash"], "evaluated_at": run["created_at"],
                 "config": run["config"], "summary": summary,
                 "message": f"{evidence.upper()} | {run['model']} | {summary['correct']}/{summary['attempts']} correct"})
    # Optional bounded-tag snapshots, not exact per-run history. Dashboard uses logs.
    series = []
    for variant, result in summary["variants"].items():
        values = {"accuracy": result["accuracy_e2e"], "coverage": result["valid"] / result["attempts"],
                  "attempts": result["attempts"]}
        for metric, value in values.items():
            series.append({"metric": f"ruleshift.eval.{metric}", "type": 3,
                           "points": [{"timestamp": now, "value": value}],
                           "tags": base_tags + [f"variant:{variant}"]})
    return {"logs": logs, "metrics": {"series": series}, "summary": summary}


def export_payloads(store, run_id, directory, include_content=False, site=None):
    data = payloads(store, run_id, include_content)
    dest = Path(directory) / run_id / "datadog"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("logs", "metrics"):
        (dest / f"{name}.json").write_text(json.dumps(data[name], indent=2))
    (dest / "manifest.json").write_text(json.dumps({"run_id": run_id, "endpoints": endpoints(site),
        "include_content": include_content, "log_events": len(data["logs"]), "sent": False,
        "note": "Offline payload preview. Run IDs and case IDs are log attributes, not metric tags."}, indent=2))
    return dest, data


def post_json(url, payload, headers, timeout=30):
    request = urllib.request.Request(url, json.dumps(payload).encode(),
                                     {"Content-Type": "application/json", **headers})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Datadog HTTP {error.code}; check DD_SITE, keys, permissions and payload") from None
    except (OSError, urllib.error.URLError):
        raise RuntimeError("Datadog connection failed; no automatic retry was performed") from None


def batches(events, max_bytes=3_000_000, max_events=500):
    batch, size = [], 2
    for event in events:
        cost = len(json.dumps(event).encode()) + 1
        if cost > max_bytes:
            raise ValueError("Datadog log event exceeds the batch limit")
        if batch and (size + cost > max_bytes or len(batch) >= max_events):
            yield batch
            batch, size = [], 2
        batch.append(event)
        size += cost
    if batch:
        yield batch


def publish(store, run_id, directory, include_content=False, site=None, metrics=False):
    api_key = os.getenv("DD_API_KEY")
    if not api_key:
        raise ValueError("Set DD_API_KEY in your terminal; datadog-export works without credentials")
    urls = endpoints(site)
    dest, data = export_payloads(store, run_id, directory, include_content, site)
    ledger_path = dest / "receipts.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    modes = ledger.setdefault("_content_modes", {})
    if urls["site"] in modes and modes[urls["site"]] != include_content:
        raise ValueError("This run was already published with a different content mode; use a new run to avoid duplicate attempt logs")
    modes[urls["site"]] = include_content
    accepted, skipped = 0, 0
    def send(kind, payload, identity):
        nonlocal accepted, skipped
        receipt_key = f"{urls['site']}:{kind}:{identity}"
        if receipt_key in ledger:
            skipped += 1
            return
        post_json(urls[kind], payload, {"DD-API-KEY": api_key})
        ledger[receipt_key] = {"accepted_at": int(time.time()), "events": len(payload) if kind == "logs" else None}
        temporary = ledger_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger, indent=2))
        temporary.replace(ledger_path)
        accepted += 1
    # IDs/content are stable; intake timestamps intentionally reflect this publication.
    # A receipt prevents ordinary republishing, not duplicates after an ambiguous timeout.
    for batch in batches(data["logs"]):
        stable = [{k: v for k, v in item.items() if k != "timestamp"} for item in batch]
        identity = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        send("logs", batch, identity)
    if metrics:
        identity = hashlib.sha256(json.dumps({"run_id": run_id, "series": [
            {k: v for k, v in s.items() if k != "points"} for s in data["metrics"]["series"]]}, sort_keys=True).encode()).hexdigest()
        send("metrics", data["metrics"], identity)
    return {"run_id": run_id, "site": urls["site"], "accepted_requests": accepted,
            "skipped_requests": skipped, "log_events": len(data["logs"]), "metrics": metrics,
            "note": "Intake acceptance is not confirmation of indexing or dashboard creation."}


def dashboard(run_id=None, evidence="simulated"):
    if run_id is not None and not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Expected a generated RuleShift run ID")
    query = "service:ruleshift $run_id $evidence @event_type:attempt"
    widgets = [{"definition": {"type": "note", "background_color": "gray", "font_size": "14",
        "content": "# RuleShift — does the rule survive?\nSelect ONE run_id and its evidence mode. Simulated controls are not LLM results. Empty panels can mean no matching indexed logs or missing numeric measures. Do not compare blended runs.\nThis tests task robustness, not philosophical understanding or training-data membership. Exact prompts stay local unless --include-content is used."}}]
    def log_query(name, metric=None, aggregation="avg", group=False, extra=""):
        item = {"name": name, "data_source": "logs", "indexes": ["*"],
                "search": {"query": query + extra}, "compute": {"aggregation": aggregation}}
        if metric:
            item["compute"]["metric"] = metric
        if group:
            item["group_by"] = [{"facet": "@variant", "limit": 10,
                                  "sort": {"aggregation": "count", "order": "desc"}}]
        return item
    def number(title, q, formula, unit=None):
        definition = {"type": "query_value", "title": title, "autoscale": False, "precision": 1,
                      "requests": [{"queries": [q], "formulas": [{"formula": formula}], "response_format": "scalar"}]}
        if unit:
            definition["custom_unit"] = unit
        widgets.append({"definition": definition})
    number("Correct / all attempts", log_query("a", "@correct"), "100 * a", "%")
    number("Valid response coverage", log_query("a", "@valid"), "100 * a", "%")
    number("Recorded attempts", log_query("a", aggregation="count"), "a")
    number("P95 request latency", log_query("a", "@latency_ms", "pc95"), "a", "ms")
    table_queries = [log_query("a", "@correct", group=True), log_query("b", "@valid", group=True),
                     log_query("c", aggregation="count", group=True)]
    widgets.append({"definition": {"type": "query_table", "title": "Accuracy and coverage by variation",
        "requests": [{"queries": table_queries, "formulas": [
            {"formula": "100 * a", "alias": "E2E accuracy %"},
            {"formula": "100 * b", "alias": "Valid %"}, {"formula": "c", "alias": "Attempts"}],
            "response_format": "scalar"}]}})
    for title, metric in (("Correct baseline → wrong variant", "@paired_regression"),
                          ("Answer changed on invariant input", "@invariance_violation")):
        number(title, log_query("a", metric, "sum"), "a")
    widgets.append({"definition": {"type": "log_stream", "title": "Failing attempts — click to inspect",
        "query": query + " @correct:0", "indexes": ["*"],
        "columns": ["@case_id", "@variant", "@status", "@expected", "@observed", "@latency_ms"],
        "show_date_column": True, "show_message_column": True}})
    widgets.append({"definition": {"type": "log_stream", "title": "Run summary / uncertainty / protocol",
        "query": "service:ruleshift $run_id $evidence @event_type:run_summary", "indexes": ["*"],
        "columns": ["@run_id", "@model", "@dataset_hash"], "show_message_column": True}})
    return {"title": "RuleShift — Controlled Generalization Lab", "layout_type": "ordered",
            "description": "Run-scoped robustness diagnostics. Configure log measures before using analytics panels.",
            "template_variables": [{"name": "run_id", "prefix": "@run_id", "default": run_id or "select_one_run"},
                                   {"name": "evidence", "prefix": "evidence", "default": evidence}],
            "widgets": widgets}


def create_dashboard(payload, site=None):
    api_key, app_key = os.getenv("DD_API_KEY"), os.getenv("DD_APP_KEY") or os.getenv("DD_APPLICATION_KEY")
    if not api_key or not app_key:
        raise ValueError("Dashboard creation needs DD_API_KEY and DD_APP_KEY (dashboard write permission)")
    return post_json(endpoints(site)["dashboard"], payload, {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key})
