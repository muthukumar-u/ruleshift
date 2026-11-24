from collections import defaultdict
from datetime import datetime, timezone
import json
import random
import time
import uuid

from .cases import VERSION, dataset_hash
from .providers import parse_answer


def evaluate(store, cases, provider, repeats=1, progress=None):
    if not cases or not 1 <= repeats <= 20:
        raise ValueError("a nonempty suite and repeats between 1 and 20 are required")
    run_id = uuid.uuid4().hex
    store.add_run({"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                   "model": provider.model, "provider": provider.kind,
                   "simulated": int(provider.simulated), "dataset_hash": dataset_hash(cases),
                   "config": json.dumps({**provider.config(), "template_version": VERSION,
                                         "repeats": repeats, "cases": len(cases),
                                         "start_seed": min(c.seed for c in cases),
                                         "seed_count": len({c.seed for c in cases}),
                                         "families": sorted({c.family for c in cases})}, sort_keys=True)})
    jobs = [(case, repeat) for case in cases for repeat in range(repeats)]
    # Stable shuffle reduces correlation between transformation type and request order.
    random.Random(7349).shuffle(jobs)
    for index, (case, repeat) in enumerate(jobs, 1):
        started = time.perf_counter()
        raw, observed, status, inp, out = "", None, "ok", None, None
        try:
            response = provider.call(case.prompt)
            raw, inp, out = response.raw, response.input_tokens, response.output_tokens
            try:
                observed = parse_answer(raw, case.expected)
            except (ValueError, TypeError, OverflowError):
                status = "parse_error"
        except Exception as error:
            # Persist the error type only; remote errors can contain credentials.
            status, raw = "provider_error", f"Provider failure ({type(error).__name__})"
        record = {"run_id": run_id, "case_id": case.case_id, "repeat_index": repeat,
                  "family": case.family, "template_id": case.template_id, "seed": case.seed,
                  "variant": case.variant, "relation": case.relation, "prompt": case.prompt,
                  "expected": json.dumps(case.expected),
                  "observed": None if status != "ok" else json.dumps(observed),
                  "raw_response": raw, "status": status,
                  "correct": int(status == "ok" and observed == case.expected),
                  "latency_ms": (time.perf_counter() - started) * 1000,
                  "input_tokens": inp, "output_tokens": out}
        store.add_result(record)
        if progress:
            progress(index, len(jobs))
    return run_id


def cluster_interval(rows, samples=1000):
    """95% percentile bootstrap over template+seed groups, not repeat rows.

    Conditional on these selected templates; not a claim about all tasks.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[(row["template_id"], row["seed"])].append(row["correct"])
    values = list(groups.values())
    if len(values) < 2:
        return None
    rng = random.Random(991)
    estimates = []
    for _ in range(samples):
        selected = [rng.choice(values) for _ in values]
        estimates.append(sum(map(sum, selected)) / sum(map(len, selected)))
    estimates.sort()
    return [estimates[int(samples * .025)], estimates[int(samples * .975)]]


def summarize(rows):
    variants = {}
    for variant in sorted({r["variant"] for r in rows}):
        group = [r for r in rows if r["variant"] == variant]
        valid = [r for r in group if r["status"] == "ok"]
        correct = sum(r["correct"] for r in group)
        variants[variant] = {"attempts": len(group), "valid": len(valid), "correct": correct,
                             "accuracy_e2e": correct / len(group),
                             "accuracy_valid": correct / len(valid) if valid else None,
                             "ci95_cluster": cluster_interval(group)}
    baseline = {(r["template_id"], r["seed"], r["repeat_index"]): r
                for r in rows if r["variant"] == "baseline"}
    pairs = defaultdict(lambda: {"valid_pairs": 0, "baseline_correct_pairs": 0,
                                  "retained_correct": 0, "invariance_violations": 0,
                                  "baseline_correct_variant_wrong": 0})
    for row in rows:
        if row["variant"] == "baseline":
            continue
        base = baseline.get((row["template_id"], row["seed"], row["repeat_index"]))
        if not base or base["status"] != "ok" or row["status"] != "ok":
            continue
        pair = pairs[row["variant"]]
        pair["valid_pairs"] += 1
        if base["correct"]:
            pair["baseline_correct_pairs"] += 1
            pair["retained_correct"] += row["correct"]
            pair["baseline_correct_variant_wrong"] += 1 - row["correct"]
        if row["relation"] == "invariant" and json.loads(base["observed"]) != json.loads(row["observed"]):
            pair["invariance_violations"] += 1
    repeated_baselines = defaultdict(list)
    for row in rows:
        if row["variant"] == "baseline" and row["status"] == "ok":
            repeated_baselines[(row["template_id"], row["seed"])].append(json.loads(row["observed"]))
    eligible = [values for values in repeated_baselines.values() if len(values) >= 2]
    unstable = sum(len(set(values)) > 1 for values in eligible)
    return {"attempts": len(rows), "valid": sum(r["status"] == "ok" for r in rows),
            "correct": sum(r["correct"] for r in rows),
            "parse_errors": sum(r["status"] == "parse_error" for r in rows),
            "provider_errors": sum(r["status"] == "provider_error" for r in rows),
            "variants": variants, "paired": dict(pairs),
            "baseline_repeat_variability": {"eligible_groups": len(eligible), "disagreeing_groups": unstable,
                                            "rate": unstable / len(eligible) if eligible else None},
            "interpretation": "Behavioral robustness on selected templates, not proof of memorization or understanding."}
