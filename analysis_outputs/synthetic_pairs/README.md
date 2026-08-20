# Combined synthetic pairs (v4 + v5 + v6)

Concatenation of v4, v5, and v6 synthetic contrast-pair corpora. Source directories under `analysis_outputs/v4_synthetic_pairs/`, `v5_synthetic_pairs/`, and `v6_synthetic_pairs/` were not modified.

## Files

| File | Schema | v4 | v5 | v6 | Total |
| --- | --- | ---: | ---: | ---: | ---: |
| `trajectories.jsonl` | `MinedTrajectory` | 30 | 120 | 1100 | 1250 |
| `safety_trajectories_synthetic.jsonl` | legacy safety trajectory | 30 | 120 | 1100 | 1250 |
| `pairs.jsonl` | `ContrastPair` | 15 | 60 | 550 | 625 |

Records are written **v4 then v5 then v6**. Every line has `"corpus": "v4"`, `"v5"`, or `"v6"` in addition to the original fields.

## Regenerate

```bash
cd benchmarks/safety_monitor && uv run python -m safety_monitor v6-synth --merge
```
