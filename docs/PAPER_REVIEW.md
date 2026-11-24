# Selected paper and implementation decisions

Read: [GSM-Symbolic, arXiv v2, 27 August 2025](https://arxiv.org/html/2410.05229v2), including sections 3–4 and appendices A.6/B. [Official code/data](https://github.com/apple-aiml-research/ml-gsm-symbolic).

## What the study does

The authors turn selected GSM8K questions into templates with variables and validity constraints. Their main setup uses 100 templates and 50 instantiations each, commonly with eight worked examples and greedy decoding. They vary names, numeric values, clause complexity and irrelevant details. Reported performance changes motivate their interpretation that reasoning is fragile. Arithmetic checks in appendix A.6 address one potential confound. Appendix B discusses criticism of statistical assumptions. These experiments do not directly observe a model's training history or establish a universal memorization detector.

Also read the primary [ICLR statistical critique](https://iclr-blogposts.github.io/2025/blog/towards-more-rigorous-llm-evals/). It challenges significance claims and alternative explanations; those concerns inform our paired reporting and restrained conclusions.

## What we implement independently

RuleShift uses the controlled-variation idea, with newly authored templates and deterministic answer functions. It does not import Apple's questions, templates, or data. This avoids claiming benchmark reproduction and keeps the prototype's oracle inspectable.

| Engineering decision | Reason |
|---|---|
| Separate invariant changes from answer-changing changes | Renaming should preserve the answer; a rule change may legitimately alter it. |
| Pair baseline/variant by template, seed and repeat | Find the specific behavioral change rather than compare unrelated task averages. |
| Keep provider errors and parse errors visible | A timeout is not a demonstrated mathematical reasoning error. |
| Report E2E and valid-only accuracy together | Avoid hiding transport/format failures through selective denominators. |
| Repeat baseline prompts | Establish whether identical inputs already produce different answers. |
| Cluster intervals by task instance | Do not treat reruns of one prompt as independent new tasks. |
| Preserve prompt, answer, response, settings and hash | Make each reported result inspectable and reproducible at the protocol level. |
| Require matching datasets/settings for comparison | Changing prompts or sample counts can change scores independently of model quality. |

## Deliberate deviations

- Three original templates, rather than the paper's benchmark breadth.
- Zero-shot, structured final answers rather than eight-shot chain-of-thought prompts.
- Plainly irrelevant distractors rather than subtle apparently operational clauses.
- Added refund-policy rules and boundaries; these are our extension.
- No inference about hidden model mechanisms, contamination, or philosophical understanding.

Consequently the results are not directly comparable to the paper's published scores. Testing current models is a new experiment, not a validation of historical results.

## What makes a stronger next experiment

Human-review new template families before running models. Keep arithmetic ranges and operation counts matched where possible. Lock the prompt/protocol in advance. Use disjoint development and private structural holdouts, not just unseen numbers from the same templates. Run multiple baselines and report full failure records. Compare a proposed prompt improvement only on untouched cases. Investigate arithmetic, format, retrieval and rule-binding failures separately instead of assigning every failure to memorization.

This is a diagnostic tool. It reveals reproducible weaknesses that developers can act on; it does not certify that an LLM understands a task.
