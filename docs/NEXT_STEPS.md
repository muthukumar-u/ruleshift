# From working prototype to hackathon entry

## Today

1. Read START_HERE.md, run the offline demo, and inspect a simulated distraction failure in its local report. These are pipeline controls, not findings about real models.
2. Read sections 3/4 and appendices A.6/B of the selected paper, then the linked statistical critique in PAPER_REVIEW.md.
3. Choose a local model or a provider/model you already have access to. Run 10 seeds and 3 repeats using the README command. This is 540 calls per model for all suites; start with 1 seed if you only want to check connectivity.
4. Verify coverage. A zero-valid-response run exits with code 2; inspect its export before interpreting any accuracy.
5. Follow GRAFANA_SETUP.md to publish a selected real run into local PostgreSQL and inspect its actual failures in Grafana. A model may pass this small suite; that is a legitimate result, not a reason to fabricate a weakness.

## Make it scientifically stronger

- Add 10–20 human-reviewed policy templates with exceptions, precedence and positive/negative outcomes. Keep expected decisions executable in ordinary code.
- Reserve structurally different policy families before prompt tuning. Different numeric seeds alone do not establish structural generalization.
- Add answer-preserving paraphrases only after a reviewer verifies equivalence. An LLM-generated paraphrase can accidentally change the task.
- Add a matched arithmetic-control suite to distinguish calculation failures from word-problem failures.
- Save a fresh baseline and a proposed improvement with versioned prompts, then compare on the private holdout. The current generic connector uses a fixed prompt; version that explicitly when adding interventions.
- Report uncertainty and baseline response variability. Avoid selecting only examples that make a favorite model look good or bad.

## Kiro and submission provenance

This initial code was prepared with Codex. No Kiro session has been run and no Kiro-generated evidence has been fabricated. Existing working code alone does not fulfill Kiroween's Kiro-use requirement.

Open this project in Kiro and genuinely use it to develop the next features: reviewed policy templates, arithmetic controls, or a better failure inspector. Create requirements/design/tasks through your actual Kiro workflow, use steering/specs/hooks where useful, keep the resulting `.kiro` directory, and record what Kiro contributed. Preserve accurate provenance and review the current [official rules](https://kiroween.devpost.com/rules) before selecting a category. Do not claim that this is novel research or that this small evaluation determines memorization.

## Three-minute demo outline

- 0:00–0:25: A model answers a task correctly. Explain why a single correct answer is incomplete evidence.
- 0:25–1:05: Show controlled name/number/distraction/rule variants. Show the code-verified expected answers.
- 1:05–1:45: Open Grafana on a selected real model run. Inspect a paired failure, if observed, alongside valid-response coverage and uncertainty intervals.
- 1:45–2:20: Explain a measured improvement on unseen holdout tasks, if implemented. Show successes and remaining failures.
- 2:20–3:00: Show genuine Kiro contributions, reproducible run settings, and the project's practical limit: robustness evidence, not mind reading.

Keep simulated mode clearly labeled if it is used for a pipeline demonstration. Actual results should identify the model version and protocol. Source publication, external hosting, and hackathon submission have not been performed.
