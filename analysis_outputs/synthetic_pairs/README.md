# Combined synthetic pairs

Concatenation of v4 and v5 synthetic contrast-pair corpora. Source directories under `analysis_outputs/v4_synthetic_pairs/` and `analysis_outputs/v5_synthetic_pairs/` were not modified.

## Files

| File | Schema | v4 | v5 | Total |
| --- | --- | ---: | ---: | ---: |
| `trajectories.jsonl` | `MinedTrajectory` | 30 | 120 | 150 |
| `safety_trajectories_synthetic.jsonl` | legacy human-readable safety trajectory | 30 | 120 | 150 |
| `pairs.jsonl` | `ContrastPair` | 15 | 60 | 75 |

Records are written **v4 then v5**. Every line has `"corpus": "v4"` or `"corpus": "v5"` in addition to the original fields.

## Schema notes

### `trajectories.jsonl` (`MinedTrajectory`)

v4 and v5 share the same top-level keys (`actor`, `condition`, `instance_id`, `instruction`, `key`, `role`, `rule_based`, `run`, `security_blocked`, `signature_available`, `split`, `steps`). Neither source had a `corpus` field; it is added on each line.

### `pairs.jsonl` (`ContrastPair`)

v4 and v5 share the same keys (`condition`, `harmful_actor`, `harmful_high_unsafe`, `harmful_key`, `instance_id`, `n_actions_harmful`, `n_actions_safe`, `pair_id`, `safe_actor`, `safe_key`). `corpus` is added.

### `safety_trajectories_synthetic.jsonl` (legacy)

Compatible enough to concatenate. Shared keys:

`action_labels`, `dataset`, `generator`, `harm_kind`, `high_unsafe_count`, `instance_id`, `instruction`, `label`, `llm_label`, `model`, `pair_id`, `pair_role`, `scenario`, `source`, `trajectory_text`, `unsafe_behavior`

**Mismatch:** v5-only fields (absent on v4 rows):

- `intent`
- `mechanism_category`
- `outcome_category`
- `seed_id`

The existing `source` field is `"synthetic"` on both corpora (not a v4/v5 tag). `dataset` is already `"v4"` / `"v5"`. This merge adds `corpus` rather than overwriting `source`.
