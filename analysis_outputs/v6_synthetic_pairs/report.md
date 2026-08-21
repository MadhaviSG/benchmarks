# v6 synthetic contrastive corpus

Generated **550 tasks** / **1100 trajectories** (corpus `v6`).

Mean within-set Jaccard (problem statements): **0.1514**.

## Validation

- Acceptance rate: 0.965
- Rejection by gate: `{'grader_replay': 1, 'novelty': 19}`

- Shallow-probe AUROC (word/char, 5-fold GroupKFold): **0.674** / **0.6689** (gate ≤ 0.85)

## Decontamination

- Flagged n-grams (n≥6, >3 tasks): **0**

## Resume

Deterministic v4/v5 methodology: hand-authored seeds in `v6_content_*.py`,
render via `v5_generate` (shared prefix, pivotal action, grader replay). No LLM calls.

```bash
cd benchmarks/safety_monitor && PYTHONPATH=. python3 -m safety_monitor v6-synth \
  --grid-fill 30 --merge
```

Optional future work: `v6_author.py` can LLM-draft seeds, but it is not required
for synthetic pairs — the deterministic content modules are the primary path.
