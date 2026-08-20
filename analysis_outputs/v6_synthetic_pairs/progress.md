# v6 progress

- Shipped: **550 tasks** (1100 trajectories)
- Target: **550 v6 tasks** / 1100 trajectories
- Remaining to 550: **0** tasks
- Mean Jaccard: 0.1499
- Grid cells filled: 139
- Rejection by gate: {'grader_replay': 1, 'novelty': 25}

**Methodology:** deterministic v4/v5 path — hand-authored `v6_content_*.py` seeds, rendered with `v5_generate` (shared prefix, single pivot, grader replay). **Zero LLM calls** for seed or trajectory drafting.

- Regenerated merge: `analysis_outputs/synthetic_pairs/` (v4+v5+v6)

Regenerate:

```bash
cd benchmarks/safety_monitor && uv run python -m safety_monitor v6-synth \
  --grid-fill 25 --merge
```

Optional future work: LLM drafting via `v6-author` is not required for synthetic pairs.
