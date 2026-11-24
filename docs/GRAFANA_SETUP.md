# Local Grafana setup

RuleShift uses **self-hosted Grafana + PostgreSQL** for visualization. No Datadog account, Grafana Cloud account or observability-service API key is needed. Your machine provides compute/storage; hosted model calls and any future cloud hosting have separate costs.

## Start the dashboard

Requirements: Python 3.11+, Docker and Compose.

```bash
cd /Users/muthu/Programs/personal/ruleshift
docker compose up -d
python3 -m ruleshift demo --seeds 10 --repeats 2 --publish
```

Open **http://localhost:3018/d/ruleshift/ruleshift**.

Local development credentials: `admin` / `ruleshift-local-dev`. Published ports bind to `127.0.0.1`. Optional port/password settings are in `.env.example`; copy it to `.env` before the first start if needed. Changing initialization passwords after database volumes exist also requires updating the corresponding database roles. Do not share this local development setup as a public deployment.

The demo evaluates two scripted controls. Their scores demonstrate the pipeline; they are not real-model results. Choose `simulated` under Evidence, then select an experiment.

## Publish existing or real-model runs

```bash
python3 -m ruleshift list
python3 -m ruleshift publish YOUR_RUN_ID
```

For a new evaluation, add `--publish`:

```bash
python3 -m ruleshift run --provider ollama --model YOUR_LOCAL_MODEL --seeds 10 --repeats 3 --publish
```

The model must already be installed in a running Ollama server. The Claude, Responses, Gemini and compatible-chat adapters also support `--publish`; their provider keys are separate from local Grafana credentials. See PROVIDERS.md.

Select `real` and the corresponding experiment to inspect real results. Only complete runs can be published. If a dashboard is already open, reload it to refresh its experiment selector after a new run.

## How publication works

SQLite is the source of truth. `publish` mirrors a completed experiment into PostgreSQL in one transaction. Primary keys prevent ordinary repeated publication from duplicating attempts. Grafana connects with a SELECT-only database role.

There are no cloud log-intake calls or custom-metric charges in this path. Full prompts, answer keys, raw responses, statuses, requested settings and timings are available in the local experiment records.

The default publisher uses `docker compose exec ... psql`, so no Python database driver is required. For an explicitly configured external PostgreSQL server, install `pip install ".[postgres]"`, provision `monitoring/postgres/schema.sql`, and set `RULESHIFT_POSTGRES_DSN`. That optional configuration changes the destination; the default is the local Compose database.

## If an agent sandbox blocks Docker access from Python

The exported `reports/YOUR_RUN_ID/publish.sql` contains the same transaction. Import it directly:

```bash
docker cp reports/YOUR_RUN_ID/publish.sql ruleshift-postgres-1:/tmp/publish.sql
docker compose exec -T postgres psql -U ruleshift -d ruleshift -v ON_ERROR_STOP=1 -f /tmp/publish.sql
```

Use the real run ID, not the placeholder. This fallback is only for restricted execution environments; an ordinary terminal can normally use `publish` directly.

## What to inspect

- E2E accuracy and valid-response coverage.
- Accuracy by transformation, alongside valid-only accuracy.
- Paired correct-baseline → wrong-variant counts and invariant violations.
- Conditional 95% cluster-bootstrap intervals.
- Failure table: expected/observed answers, baseline, changed prompt and raw response.
- Request latency and full run provenance.
- Same-prompt baseline repeat variability.
- Latest matching experiment per provider/model, restricted to identical dataset hashes and recorded requested settings.

Every panel is scoped to the chosen experiment. The global time picker does not filter it. Inspect a cell's full value when a prompt is too wide for the table. The failure query displays up to 500 rows; JSON/CSV reports preserve all attempts.

A small-suite score does not prove memorization or understanding. Model/API aliases and vendor internals can change; use available versioned IDs and keep interpretations tied to the tested tasks.

## Manage and verify

```bash
docker compose ps
python3 monitoring/build_dashboard.py
docker compose stop
```

`stop` leaves database/dashboard volumes intact. Existing RuleShift runs and archived earlier Datadog payloads have not been deleted.

The optional Playwright script `tests/grafana_smoke.cjs` checks all 13 panel queries, login, the rendered score and failure table for a simulated 10-seed, 2-repeat shortcut run. Set `RULESHIFT_SMOKE_RUN` to that run ID, `GRAFANA_URL` to the dashboard server, and `GRAFANA_PASSWORD` if changed. In a browser container, mount the repository at `/artifacts` for screenshots.
