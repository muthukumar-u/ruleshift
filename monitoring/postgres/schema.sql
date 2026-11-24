CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, model TEXT NOT NULL,
 provider TEXT NOT NULL, simulated INTEGER NOT NULL CHECK (simulated IN (0,1)),
 dataset_hash TEXT NOT NULL, config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
 run_id TEXT NOT NULL REFERENCES runs(run_id), case_id TEXT NOT NULL,
 repeat_index INTEGER NOT NULL, family TEXT NOT NULL, template_id TEXT NOT NULL,
 seed INTEGER NOT NULL, variant TEXT NOT NULL, relation TEXT NOT NULL,
 prompt TEXT NOT NULL, expected TEXT NOT NULL, observed TEXT, raw_response TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('ok','parse_error','provider_error')),
 correct INTEGER NOT NULL CHECK (correct IN (0,1)), latency_ms DOUBLE PRECISION NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER,
 PRIMARY KEY(run_id,case_id,repeat_index)
);
CREATE TABLE IF NOT EXISTS run_summaries (
 run_id TEXT NOT NULL REFERENCES runs(run_id), variant TEXT NOT NULL,
 attempts INTEGER NOT NULL, valid INTEGER NOT NULL, correct INTEGER NOT NULL,
 ci_low DOUBLE PRECISION, ci_high DOUBLE PRECISION,
 PRIMARY KEY(run_id,variant)
);
CREATE INDEX IF NOT EXISTS results_run_variant ON results(run_id,variant);
