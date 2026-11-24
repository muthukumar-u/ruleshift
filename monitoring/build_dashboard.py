"""Regenerate the provisioned Grafana dashboard: python3 monitoring/build_dashboard.py."""
import json
from pathlib import Path

DS = {"type": "grafana-postgresql-datasource", "uid": "ruleshift-postgres"}
RUN = "${run:sqlstring}"
SELECTED = f"r.run_id={RUN}"
panels = []


def sql_panel(title, sql, kind, x, y, w, h, unit=None, description=""):
    panel = {"id": len(panels) + 1, "title": title, "type": kind, "datasource": DS,
             "gridPos": {"x": x, "y": y, "w": w, "h": h}, "description": description,
             "targets": [{"refId": "A", "datasource": DS, "rawSql": sql,
                          "format": "table", "editorMode": "code", "rawQuery": True}],
             "fieldConfig": {"defaults": {"custom": {}}, "overrides": []}, "options": {}}
    if unit:
        panel["fieldConfig"]["defaults"].update({"unit": unit, "decimals": 1})
    if kind == "stat":
        panel["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                            "colorMode": "value", "graphMode": "none", "textMode": "auto"}
        panel["fieldConfig"]["defaults"]["color"] = {"mode": "fixed", "fixedColor": "blue"}
        if "failures" in title:
            panel["fieldConfig"]["defaults"].update({"color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}})
        elif "coverage" in title:
            panel["fieldConfig"]["defaults"].update({"color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "yellow", "value": 80}, {"color": "green", "value": 99}]}})
    if kind == "barchart":
        panel["options"] = {"orientation": "vertical", "showValue": "always",
                            "legend": {"displayMode": "list", "placement": "bottom"},
                            "tooltip": {"mode": "multi"}}
    if kind == "table":
        panel["options"] = {"showHeader": True, "cellHeight": "md"}
        panel["fieldConfig"]["defaults"]["custom"] = {"cellOptions": {"type": "auto"}, "filterable": True,
                                                        "minWidth": 65, "inspect": True, "wrapHeaderText": True}
    panels.append(panel)


panels.append({"id": 1, "title": "RuleShift / Controlled generalization lab", "type": "text",
               "gridPos": {"x": 0, "y": 0, "w": 24, "h": 4},
               "options": {"mode": "markdown", "content": "**Evidence mode: ${mode}.** SIMULATED runs are scripted controls, not LLM results. Switch to real only after evaluating a model.\n\nInspired by [GSM-Symbolic](https://arxiv.org/abs/2410.05229). Measures robustness on selected tasks; does not prove memorization or understanding. Numbers, rules and boundaries require recomputing the answer; names and irrelevant details preserve it. Select a run above, then inspect its paired failures below. Refresh after a new run."}})

sql_panel("Correct / all attempted", f"SELECT 100.0*avg(correct) AS accuracy FROM results r WHERE {SELECTED}",
          "stat", 0, 4, 6, 4, "percent", "End-to-end accuracy: provider and parse errors count as failures.")
sql_panel("Valid response coverage", f"SELECT 100.0*avg(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS coverage FROM results r WHERE {SELECTED}",
          "stat", 6, 4, 6, 4, "percent")
sql_panel("Recorded attempts", f"SELECT count(*) AS attempts FROM results r WHERE {SELECTED}", "stat", 12, 4, 6, 4)
sql_panel("Parsing / provider failures", f"SELECT count(*) AS failures FROM results r WHERE {SELECTED} AND status<>'ok'", "stat", 18, 4, 6, 4)

sql_panel("Where does performance break?", f"""
SELECT variant,
 round(100.0*avg(correct),1) AS "All attempts",
 round(100.0*sum(correct)/NULLIF(count(*) FILTER (WHERE status='ok'),0),1) AS "Valid responses only"
FROM results r WHERE {SELECTED} GROUP BY variant ORDER BY variant
""", "barchart", 0, 8, 14, 9, "percent", "Interpret valid-only accuracy together with coverage; missing responses must not disappear.")
sql_panel("Uncertainty / 95% cluster bootstrap", f"""
SELECT variant, correct::text || '/' || attempts::text AS "Correct / total",
 round((100.0*ci_low)::numeric,1) AS "CI low %",
 round((100.0*ci_high)::numeric,1) AS "CI high %"
FROM run_summaries WHERE run_id={RUN} ORDER BY variant
""", "table", 14, 8, 10, 9, description="Resamples template+seed groups (1000 draws), keeping repeats together. Conditional on the selected templates, not all tasks. Null with fewer than two clusters. Tiny demo data and perfect controls can yield degenerate intervals.")

sql_panel("Paired changes / same template, seed and repeat", f"""
SELECT r.variant,
 count(*) AS "Valid pairs",
 count(*) FILTER (WHERE b.correct=1) AS "Baseline correct",
 count(*) FILTER (WHERE b.correct=1 AND r.correct=0) AS "Correct → wrong",
 count(*) FILTER (WHERE b.correct=1 AND r.correct=1) AS "Correct retained",
 count(*) FILTER (WHERE r.relation='invariant' AND r.observed::jsonb<>b.observed::jsonb) AS "Invariant violations"
FROM results r JOIN results b ON r.run_id=b.run_id AND r.template_id=b.template_id
 AND r.seed=b.seed AND r.repeat_index=b.repeat_index AND b.variant='baseline'
WHERE {SELECTED} AND r.variant<>'baseline' AND r.status='ok' AND b.status='ok'
GROUP BY r.variant ORDER BY r.variant
""", "table", 0, 17, 14, 8,
          description="Only valid pairs; format and transport failures are shown separately. An invariant violation can also occur when both answers are wrong. Correct → wrong is a paired behavioral failure, not evidence of training-data membership.")
sql_panel("Latency by variation", f"""
SELECT variant, round(avg(latency_ms)::numeric,2) AS "Mean ms",
 round(percentile_cont(.95) WITHIN GROUP (ORDER BY latency_ms)::numeric,2) AS "P95 ms"
FROM results r WHERE {SELECTED} GROUP BY variant ORDER BY variant
""", "barchart", 14, 17, 10, 8, "ms", "Measured wall-clock request latency; simulated controls have no model-inference latency.")

sql_panel("Failure microscope / inspect a cell to read the full value", f"""
SELECT r.case_id, r.expected AS "Expected", r.observed AS "Observed", r.status,
 r.prompt AS "Changed question", b.prompt AS "Baseline question",
 b.observed AS "Baseline answer", r.raw_response AS "Raw response", r.repeat_index,
 round(r.latency_ms::numeric,2) AS "Latency ms"
FROM results r LEFT JOIN results b ON r.run_id=b.run_id AND r.template_id=b.template_id
 AND r.seed=b.seed AND r.repeat_index=b.repeat_index AND b.variant='baseline'
WHERE {SELECTED} AND r.correct=0 ORDER BY r.variant,r.case_id,r.repeat_index LIMIT 500
""", "table", 0, 25, 24, 12, description="Wrong answers, parsing errors and transport errors are kept distinct. The limit is 500 rows; the JSON and CSV exports contain every attempt.")
for name, width in (("case_id", 230), ("Expected", 85), ("Observed", 85), ("status", 110),
                    ("Changed question", 320), ("Baseline question", 320), ("Raw response", 180)):
    panels[-1]["fieldConfig"]["overrides"].append({"matcher": {"id": "byName", "options": name},
                                                  "properties": [{"id": "custom.width", "value": width}]})

sql_panel("Matched experiment comparison / latest run per provider + model", f"""
WITH chosen AS (SELECT * FROM runs WHERE run_id={RUN}),
eligible AS (
 SELECT a.* FROM runs a CROSS JOIN chosen c
 WHERE a.dataset_hash=c.dataset_hash AND a.simulated=c.simulated
 AND (a.config::jsonb - 'model' - 'base_url' - 'provider') =
     (c.config::jsonb - 'model' - 'base_url' - 'provider')
 AND (SELECT count(*) FROM results z WHERE z.run_id=a.run_id) =
     (a.config::jsonb->>'cases')::integer * (a.config::jsonb->>'repeats')::integer
), latest AS (
 SELECT DISTINCT ON (provider,model) * FROM eligible ORDER BY provider,model,created_at DESC
)
SELECT a.model, a.provider, count(*) AS attempts,
 round(100.0*avg(r.correct),1) AS "E2E accuracy %",
 count(*) FILTER (WHERE r.status='provider_error') AS "Provider errors",
 count(*) FILTER (WHERE r.status='parse_error') AS "Parse errors",
 a.run_id
FROM latest a JOIN results r ON r.run_id=a.run_id
GROUP BY a.model,a.provider,a.run_id ORDER BY a.model
""", "table", 0, 37, 24, 7,
          description="Requires the identical dataset hash and recorded settings (except endpoint, provider and model). Incomplete runs are excluded. Endpoint hardware may differ: latency comparison is not controlled. API aliases can change; prefer versioned model IDs.")

sql_panel("Run provenance", f"""
SELECT a.run_id, a.created_at, a.model, a.provider,
 CASE WHEN a.simulated=1 THEN 'SIMULATED CONTROL' ELSE 'REAL MODEL CALLS' END AS evidence,
 a.dataset_hash, a.config,
 (a.config::jsonb->>'cases')::integer * (a.config::jsonb->>'repeats')::integer AS "Planned attempts",
 (SELECT count(*) FROM results x WHERE x.run_id=a.run_id) AS "Recorded attempts"
FROM runs a WHERE a.run_id={RUN}
""", "table", 0, 44, 24, 6)

sql_panel("Same-prompt baseline variability", f"""
WITH groups AS (
 SELECT template_id,seed,count(*) AS valid_repeats,count(DISTINCT observed::jsonb) AS answers
 FROM results r WHERE {SELECTED} AND variant='baseline' AND status='ok'
 GROUP BY template_id,seed HAVING count(*)>=2
)
SELECT count(*) AS "Groups with 2+ valid repeats",
 count(*) FILTER (WHERE answers>1) AS "Groups with changing answers",
 round(100.0*count(*) FILTER (WHERE answers>1)/NULLIF(count(*),0),1) AS "Disagreement %"
FROM groups
""", "table", 0, 50, 12, 6,
          description="Compare transformation failures with ordinary sampling variability. With one repeat no rate is estimated. Temperature zero is not a determinism guarantee.")

sql_panel("Coverage breakdown", f"""
SELECT status,count(*) AS attempts FROM results r WHERE {SELECTED} GROUP BY status ORDER BY status
""", "table", 12, 50, 12, 6)

dashboard = {"uid": "ruleshift", "title": "RuleShift — Does the rule survive?", "schemaVersion": 39,
             "version": 1, "editable": False, "tags": ["ruleshift", "evaluation", "gsm-symbolic"],
             "timezone": "browser", "refresh": "10s", "time": {"from": "now-7d", "to": "now"},
             "description": "Run-scoped evaluation; the global time picker does not filter the selected experiment.",
             "templating": {"list": [
                 {"name": "mode", "label": "Evidence", "type": "custom", "query": "simulated,real",
                  "current": {"text": "simulated", "value": "simulated"},
                  "options": [{"text": "simulated", "value": "simulated", "selected": True},
                              {"text": "real", "value": "real", "selected": False}]},
                 {"name": "run", "label": "Experiment", "type": "query", "datasource": DS,
                  "query": "SELECT model || ' / ' || substr(created_at,1,19) || ' / ' || substr(run_id,1,8) AS __text, run_id AS __value FROM runs WHERE simulated=CASE WHEN '${mode}'='simulated' THEN 1 ELSE 0 END ORDER BY created_at DESC",
                  "refresh": 1, "sort": 0, "multi": False, "includeAll": False, "options": []}]},
             "panels": panels}
path = Path(__file__).parent / "grafana/dashboards/ruleshift.json"
path.write_text(json.dumps(dashboard, indent=2) + "\n")
print(path)
