# RuleShift — does the rule survive?

RuleShift evaluates whether a language model keeps following a task's rules when controlled details change. It produces code-verified cases, paired failure analysis, readable local reports and a self-hosted Grafana dashboard.

**New here? Read [START_HERE.md](START_HERE.md).** It explains the problem, purpose, first experiment, model compatibility and hackathon positioning in plain language.

## Quick start: no keys needed

Python 3.11+ is sufficient for offline evaluation and reports. The evaluator uses the standard library, SQLite and direct HTTP. The dashboard additionally uses Docker Compose to run Grafana and PostgreSQL locally.

```bash
cd /Users/muthu/Programs/personal/ruleshift
python3 -m ruleshift demo --seeds 3 --repeats 2
python3 -m ruleshift list
```

This evaluates **two scripted controls**, not actual LLMs. It creates Markdown, JSON, CSV and PostgreSQL import SQL in `reports/<run-id>/`.

## Real model examples

Replace model placeholders with IDs available in your account or local server. Cloud keys belong in environment variables, not command arguments.

```bash
# Ollama server running, with this model already installed:
python3 -m ruleshift check --provider ollama --model YOUR_LOCAL_MODEL
python3 -m ruleshift run --provider ollama --model YOUR_LOCAL_MODEL --seeds 3 --repeats 2

# Set ANTHROPIC_API_KEY privately first:
python3 -m ruleshift run --provider anthropic --model YOUR_CLAUDE_MODEL_ID --seeds 3 --repeats 2

# Set OPENAI_API_KEY; omit temperature explicitly if this model requires it:
python3 -m ruleshift run --provider responses --model YOUR_OPENAI_MODEL_ID --omit-temperature --seeds 3 --repeats 2

# Set GEMINI_API_KEY:
python3 -m ruleshift run --provider gemini --model YOUR_GEMINI_MODEL_ID --seeds 3 --repeats 2

# Compatible local servers and cloud gateways; RULESHIFT_API_KEY if required:
python3 -m ruleshift run --provider chat --model YOUR_MODEL_ID --base-url https://YOUR_PROVIDER/v1 --seeds 3 --repeats 2
```

`check` makes one task request and separates endpoint connectivity, response-format validity and answer correctness. It is not a model-quality certification. See [PROVIDERS.md](docs/PROVIDERS.md) for contracts and limitations.

## Open the local Grafana dashboard

```bash
docker compose up -d
python3 -m ruleshift demo --seeds 10 --repeats 2 --publish
```

Open **http://localhost:3018/d/ruleshift/ruleshift**. Local development login: `admin` / `ruleshift-local-dev`. The port is bound to `127.0.0.1`. Read [GRAFANA_SETUP.md](docs/GRAFANA_SETUP.md) for configuration, publishing, SQL-import fallback and troubleshooting.

To display an existing run, use `python3 -m ruleshift publish YOUR_RUN_ID`. For a new real-model evaluation, add `--publish` to its `run` command. Publication mirrors a complete run into local PostgreSQL; it is transactional and idempotent. SQLite remains authoritative.

Grafana and PostgreSQL run on your own machine, so no observability subscription or Datadog API key is needed. Hosting/resources and hosted model API calls can still cost money. The active CLI has no Datadog export or upload commands; earlier Datadog source and documentation are retained as inactive historical artifacts.

## Compare matching experiments

```bash
python3 -m ruleshift compare RUN_A RUN_B --output reports/comparison.json
```

Different datasets, requested protocols, incomplete runs and mixed real/simulated evidence are rejected. Deltas are descriptive, not significance tests or universal rankings. Matching requested settings does not make vendor internals, token accounting or hardware identical.

## Project map

| Path | Purpose |
|---|---|
| `ruleshift/cases.py` | Versioned templates, seeded variations, code answer keys and dataset hash |
| `ruleshift/providers.py` | Ollama, compatible Chat, Responses, Anthropic and Gemini adapters |
| `ruleshift/evaluation.py` | Strict scoring, paired analysis, repeated baselines and bootstrap intervals |
| `ruleshift/comparison.py` | Matched comparison |
| `ruleshift/storage.py` | SQLite and local PostgreSQL publication |
| `ruleshift/cli.py` | Commands and local reports |
| `compose.yaml` | Local Grafana + PostgreSQL services |
| `monitoring/build_dashboard.py` | Reproducible Grafana dashboard generation |
| `monitoring/grafana/` | Provisioned data source and dashboard |
| `docs/PAPER_REVIEW.md` | Research, statistical criticism and implementation differences |
| `docs/NEXT_STEPS.md` | Hackathon development plan and Kiro provenance |
| `tests/` | Oracle, comparison, provider/publication tests and Grafana browser check |

## Research and limits

Inspired by [GSM-Symbolic v2](https://arxiv.org/html/2410.05229v2), using original math templates and a refund-policy extension. This is not a reproduction of that benchmark. Three simple templates are a diagnostic prototype, not a validated broad benchmark.

Names and irrelevant facts preserve answers; numbers, rules and boundaries require recomputation. Failures may reflect arithmetic, wording, formatting, transport or other factors. They do not prove memorization. Fresh numeric seeds do not demonstrate structural generalization or absence from training data.

Intervals resample template+seed groups, keeping repeats together. They are conditional on selected templates, not confidence in “understanding.” Repeated identical baselines help reveal ordinary response variability.

## Verify

```bash
python3 -m unittest discover -s tests -v
```

HTTP tests bind local fixture servers. If an agent sandbox blocks sockets, use an available Python container:

```bash
docker run --rm -v "$PWD:/work:ro" -w /work python:3.12-slim python -m unittest discover -s tests -v
```

`python3 monitoring/build_dashboard.py` regenerates the provisioned dashboard. `tests/grafana_smoke.cjs` checks every panel SQL query, login, rendered accuracy and the failure table using Playwright. It expects a simulated 10-seed, 2-repeat shortcut run and writes preview screenshots under `/artifacts/docs` when the repository is mounted at `/artifacts`. Mock-provider tests do not establish real model quality.
