# Datadog setup

> Archived historical guide. Grafana is now the only active visualization integration. The Datadog commands below are no longer exposed by the CLI. Use [GRAFANA_SETUP.md](GRAFANA_SETUP.md) for the current workflow.

## 1. Preview locally

```bash
python3 -m ruleshift demo --seeds 3 --repeats 2
python3 -m ruleshift list
python3 -m ruleshift datadog-export YOUR_RUN_ID
```

Replace `YOUR_RUN_ID` with an actual printed ID. Inspect `reports/YOUR_RUN_ID/datadog/`:

- `logs.json`: attempted cases and one run-summary event.
- `metrics.json`: optional custom-metric snapshots, sent only with `--metrics`.
- `manifest.json`: intended endpoints/content setting. `sent: false` describes the offline export, not a delivery receipt.

Default logs include expected/observed answers, correctness, latency, pairing flags and IDs. Prompt text/raw responses remain local. `--include-content` explicitly includes bounded content in exports/uploads.

## 2. Set your Datadog site and credentials

Load your uncommitted `.env` as described in PROVIDERS.md. Set `DD_API_KEY` and your account's `DD_SITE`:

| Region | Site |
|---|---|
| US1 | `datadoghq.com` |
| US3 / US5 | `us3.datadoghq.com` / `us5.datadoghq.com` |
| EU | `datadoghq.eu` |
| AP1 / AP2 | `ap1.datadoghq.com` / `ap2.datadoghq.com` |
| UK1 | `uk1.datadoghq.com` |

Use the hostname without `https://` or `app.`. `DD_ENV` defaults to `local`. Model-provider keys and Datadog keys are different.

Log intake needs an API key. Only optional dashboard creation needs `DD_APP_KEY` (or `DD_APPLICATION_KEY`) with dashboard-write permission.

## 3. Upload explicitly

```bash
python3 -m ruleshift publish YOUR_RUN_ID
```

This sends logs only. Add `--metrics` for optional snapshots, or `--include-content` if you explicitly want prompt/response text in Datadog.

Datadog indexing/retention and custom metrics may be billable under your account. Offline evaluation/export needs no account. This code does not create accounts, enable trials or change billing settings.

Publication timestamps reflect intake time; `evaluated_at` retains the original evaluation time. Search indexed Logs Explorer data:

```text
service:ruleshift @run_id:YOUR_RUN_ID
```

HTTP acceptance does not establish successful indexing. Check index filters and retention if events are missing.

## 4. Configure facets and measures

Open an indexed attempt event in Logs Explorer. Create these qualitative facets for grouping/filtering if absent: `variant`, `run_id`, `model`, `provider`, `event_type`.

Create numeric **measures** for the supplied analytics widgets:

| Attribute | Meaning |
|---|---|
| `correct` | 0/1; average gives E2E accuracy |
| `valid` | 0/1; average gives coverage |
| `latency_ms` | Request latency; select milliseconds |
| `paired_regression` | 0/1; sum counts correct → wrong pairs |
| `invariance_violation` | 0/1; sum counts invariant answer changes |

These are log-analytics measures, not optional custom-metric snapshots. Without measures, analytics panels can be empty while raw logs remain visible. Basic text search does not require facets. See [Datadog's guide](https://docs.datadoghq.com/logs/explorer/facets/).

## 5. Generate/import the dashboard

```bash
python3 -m ruleshift datadog-dashboard --run-id YOUR_RUN_ID
```

Import `monitoring/datadog/dashboard.json` in Datadog, or set `DD_APP_KEY` and explicitly create it:

```bash
python3 -m ruleshift datadog-dashboard --run-id YOUR_RUN_ID --create
```

Repeated `--create` calls create additional dashboards; this is not an update command. The reusable JSON generated without a run ID deliberately selects no data until you choose a run.

Panels show accuracy, coverage, attempt count, P95 latency, variation results, paired failures, failing-event inspection and the run-summary log containing uncertainty/protocol details. Choose exactly one run ID/evidence mode and a time window including upload time. Do not use `*` to blend experiments.

Use `python3 -m ruleshift compare RUN_A RUN_B` to enforce matching data/settings before interpreting a cross-model comparison.

## Delivery and integrity

- `receipts.json` skips ordinary already-acknowledged batches. No automatic retries are made.
- A timeout after acceptance is ambiguous: retrying can duplicate events. Log intake is not exactly-once storage; stable `event_id` attributes do not guarantee Datadog deduplication.
- Changing content inclusion after publishing a run to the same site is rejected to prevent ordinary double counting. Use a new run or read full local reports instead.
- Experiment/case IDs are log attributes, not custom-metric tags. Optional snapshots show latest model/provider/variant observations, not exact per-run history.
- SQLite and JSON remain authoritative. Do not delete receipt files and repeatedly upload the same run to force a refresh.

## Troubleshooting

| Symptom | Check |
|---|---|
| Missing keys | Load variables in the same terminal; CLI does not auto-load `.env` |
| HTTP 401/403 | Account site, key type and permissions |
| Raw logs visible, analytics empty | Numeric measures and `variant` facet |
| Dashboard empty | Exact run/evidence, upload-time window and indexing |
| Datadog/local scores differ | Duplicates, partial delivery, filters or mixed experiments |
| Requests skipped | Expected delivery-receipt behavior |

Official references: [logs](https://docs.datadoghq.com/api/latest/logs/), [metrics](https://docs.datadoghq.com/api/latest/metrics/), [dashboard API](https://docs.datadoghq.com/api/latest/dashboards/).
