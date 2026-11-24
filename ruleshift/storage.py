import json
from pathlib import Path
import sqlite3
import subprocess

RUN_COLUMNS = ("run_id", "created_at", "model", "provider", "simulated", "dataset_hash", "config")
RESULT_COLUMNS = ("run_id", "case_id", "repeat_index", "family", "template_id", "seed",
                  "variant", "relation", "prompt", "expected", "observed", "raw_response",
                  "status", "correct", "latency_ms", "input_tokens", "output_tokens")
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, model TEXT NOT NULL,
 provider TEXT NOT NULL, simulated INTEGER NOT NULL, dataset_hash TEXT NOT NULL,
 config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
 run_id TEXT NOT NULL REFERENCES runs(run_id), case_id TEXT NOT NULL,
 repeat_index INTEGER NOT NULL, family TEXT NOT NULL, template_id TEXT NOT NULL,
 seed INTEGER NOT NULL, variant TEXT NOT NULL, relation TEXT NOT NULL,
 prompt TEXT NOT NULL, expected TEXT NOT NULL, observed TEXT, raw_response TEXT NOT NULL,
 status TEXT NOT NULL, correct INTEGER NOT NULL, latency_ms REAL NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER,
 PRIMARY KEY(run_id, case_id, repeat_index)
);
CREATE INDEX IF NOT EXISTS results_run_variant ON results(run_id, variant);
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def add_run(self, run):
        self.db.execute(f"INSERT INTO runs ({','.join(RUN_COLUMNS)}) VALUES ({','.join('?' for _ in RUN_COLUMNS)})",
                        [run[c] for c in RUN_COLUMNS])
        self.db.commit()

    def add_result(self, result):
        self.db.execute(f"INSERT INTO results ({','.join(RESULT_COLUMNS)}) VALUES ({','.join('?' for _ in RESULT_COLUMNS)})",
                        [result[c] for c in RESULT_COLUMNS])
        self.db.commit()

    def list_runs(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM runs ORDER BY created_at DESC")]

    def get_run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("unknown run ID")
        run = dict(row)
        run["config"] = json.loads(run["config"])
        return run

    def results(self, run_id):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM results WHERE run_id=? ORDER BY template_id,seed,variant,repeat_index", (run_id,))]

    def publish_sql(self, run_id):
        """Safe SQL transport for the local Docker database; no Python driver needed."""
        from .evaluation import summarize
        run = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ValueError("unknown run ID")
        def literal(value):
            if value is None:
                return "NULL"
            if isinstance(value, (int, float)):
                return str(value)
            # NUL cannot be stored in PostgreSQL TEXT; retain its escaped spelling.
            return "'" + str(value).replace("\x00", "\\u0000").replace("'", "''") + "'"
        def insert(table, columns, values, conflict):
            return f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(literal(v) for v in values)}) ON CONFLICT {conflict} DO NOTHING;"
        statements = ["BEGIN;", "SET LOCAL standard_conforming_strings = on;"]
        statements.append(insert("runs", RUN_COLUMNS, [run[c] for c in RUN_COLUMNS], "(run_id)"))
        rows = self.results(run_id)
        config = json.loads(run["config"])
        if len(rows) != config["cases"] * config["repeats"]:
            raise ValueError("Incomplete run: finish evaluation before publishing")
        for row in rows:
            statements.append(insert("results", RESULT_COLUMNS, [row[c] for c in RESULT_COLUMNS], "(run_id,case_id,repeat_index)"))
        for variant, summary in summarize(rows)["variants"].items():
            interval = summary["ci95_cluster"] or [None, None]
            statements.append(insert("run_summaries", ("run_id", "variant", "attempts", "valid", "correct", "ci_low", "ci_high"),
                                     (run_id, variant, summary["attempts"], summary["valid"], summary["correct"], *interval),
                                     "(run_id,variant)"))
        statements.append("COMMIT;")
        return "\n".join(statements)

    def publish(self, dsn, run_id):
        """Transactional, idempotent PostgreSQL mirror for the local Grafana dashboard."""
        if not dsn:
            try:
                result = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql",
                                         "-U", "ruleshift", "-d", "ruleshift", "-v", "ON_ERROR_STOP=1", "-q"],
                                        input=self.publish_sql(run_id), text=True, capture_output=True,
                                        cwd=Path(__file__).resolve().parents[1], timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeError("Grafana database unavailable; start docker compose up -d") from None
            if result.returncode:
                if "permission denied" in result.stderr.lower() and "docker" in result.stderr.lower():
                    raise RuntimeError("Docker socket access denied; export SQL and use docker compose exec directly")
                raise RuntimeError("PostgreSQL publish failed; check docker compose ps and schema initialization")
            return len(self.results(run_id))
        try:
            import psycopg
        except ImportError:
            raise RuntimeError('PostgreSQL publishing needs: pip install ".[postgres]"') from None
        run = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ValueError("unknown run ID")
        config = json.loads(run["config"])
        if len(self.results(run_id)) != config["cases"] * config["repeats"]:
            raise ValueError("Incomplete run: finish evaluation before publishing")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # The schema is provisioned by the PostgreSQL container.
                cur.execute(f"INSERT INTO runs ({','.join(RUN_COLUMNS)}) VALUES ({','.join('%s' for _ in RUN_COLUMNS)}) "
                            "ON CONFLICT (run_id) DO NOTHING", tuple(run[c] for c in RUN_COLUMNS))
                rows = self.results(run_id)
                cur.executemany(f"INSERT INTO results ({','.join(RESULT_COLUMNS)}) VALUES ({','.join('%s' for _ in RESULT_COLUMNS)}) "
                                "ON CONFLICT (run_id,case_id,repeat_index) DO NOTHING",
                                [tuple(r[c] for c in RESULT_COLUMNS) for r in rows])
                from .evaluation import summarize
                for variant, summary in summarize(rows)["variants"].items():
                    interval = summary["ci95_cluster"] or [None, None]
                    cur.execute("INSERT INTO run_summaries (run_id, variant, attempts, valid, correct, ci_low, ci_high) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id,variant) DO UPDATE SET "
                                "attempts=EXCLUDED.attempts, valid=EXCLUDED.valid, correct=EXCLUDED.correct, "
                                "ci_low=EXCLUDED.ci_low, ci_high=EXCLUDED.ci_high",
                                (run_id, variant, summary["attempts"], summary["valid"], summary["correct"], *interval))
        return len(rows)
