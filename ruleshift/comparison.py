"""Compare only datasets and requested protocols that match."""

from .evaluation import summarize

PROTOCOL_FIELDS = ("instruction", "parser", "temperature", "max_tokens", "timeout", "template_version",
                   "repeats", "cases", "start_seed", "seed_count", "families")


def compare(store, run_a, run_b):
    a, b = store.get_run(run_a), store.get_run(run_b)
    if a["simulated"] != b["simulated"]:
        raise ValueError("Do not compare a simulated control with a real model as equivalent evidence")
    if a["dataset_hash"] != b["dataset_hash"]:
        raise ValueError("Dataset mismatch: use identical suites, seeds and template versions")
    differences = [field for field in PROTOCOL_FIELDS if a["config"].get(field) != b["config"].get(field)]
    if differences:
        raise ValueError("Evaluation protocol mismatch: " + ", ".join(differences))
    rows_a, rows_b = store.results(run_a), store.results(run_b)
    planned = a["config"]["cases"] * a["config"]["repeats"]
    if len(rows_a) != planned or len(rows_b) != planned:
        raise ValueError("Only complete runs can be compared")
    summary_a, summary_b = summarize(rows_a), summarize(rows_b)
    return {"run_a": run_a, "run_b": run_b, "model_a": a["model"], "model_b": b["model"],
            "dataset_hash": a["dataset_hash"], "evidence": "simulated" if a["simulated"] else "real",
            "variants": {variant: {"accuracy_a": result["accuracy_e2e"],
                                  "accuracy_b": summary_b["variants"][variant]["accuracy_e2e"],
                                  "delta_percentage_points_b_minus_a": 100 * (summary_b["variants"][variant]["accuracy_e2e"] - result["accuracy_e2e"])}
                         for variant, result in summary_a["variants"].items()},
            "note": "Matched requested protocol, not identical vendor internals, token accounting or hardware. Descriptive deltas are not significance tests."}
