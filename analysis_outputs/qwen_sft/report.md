# Local Qwen SFT for the external safety critic

Local Qwen LoRA SFT **did not run**. This machine has no GPU and no on-disk Qwen weights; the numbers below are from the CPU **mock** backend (keyword zero-shot vs hashed-ngram logistic regression) so the split and metrics pipeline is verified.

## Status

- **SFT actually ran:** `False`
- **Backend used:** `mock`
- **GPU/CUDA:** `False`
- **Torch / transformers / peft:** `False` / `False` / `False`
- **Local Qwen path:** `None`
- **Probe reason:** backend=mock (forced)

## Train/eval firewall

Training uses **synthetic v4 + v5** trajectories only. Real OAS v3 runs (`analysis_outputs/critic_training_pairs`) are eval-only. The synthetic holdout is a **task-level** (`instance_id`) cut: no step from a holdout task is in train. Holdout metrics are a leakage diagnostic ("can the model fit the generator?") and **must not** be treated as the headline number.

- Train tasks: **60** (120 trajectories)
- Synthetic holdout tasks: **15** (30 trajectories)
- V3 eval trajectories: **2575** across 300 tasks
- Split salt: `qwen-sft`, holdout fraction `0.2`
- Train∩holdout instance_ids: `[]`

## Example counts (one SFT row per labeled action)

| Split | Examples | high-unsafe | low-unsafe | tasks | trajectories |
|---|---:|---:|---:|---:|---:|
| train | 1399 | 60 | 1339 | 60 | 120 |
| synthetic_holdout | 362 | 15 | 347 | 15 | 30 |
| v3_eval | 24309 | 695 | 23614 | 300 | 2575 |

## Headline: v3 eval (weak per-action labels + trajectory GT)

Per-action labels on v3 are **weak supervision** (signature + sink + contrastive lift). Trajectory roles (`harmful` / `prevented` / `clean`) and `rule_based` come from the environment checkpoint and are the stronger ground truth. See `analysis_outputs/critic_training_pairs/report.md`.

### Action-level (positive class = `high-unsafe`)

| Stage / set | n | n_pos | accuracy | precision | recall | F1 | tp/fp/tn/fn |
|---|---:|---:|---:|---:|---:|---:|---|
| before zero-shot | 24309 | 695 | 0.924 | 0.049 | 0.091 | 0.064 | 63/1226/22388/632 |
| before few-shot | 24309 | 695 | 0.924 | 0.049 | 0.091 | 0.064 | 63/1226/22388/632 |
| after SFT | 24309 | 695 | 0.433 | 0.024 | 0.479 | 0.046 | 333/13427/10187/362 |

Headline F1 (`high-unsafe` on v3): **before 0.064 → after 0.046**.

### Trajectory-level (does critic mass predict `role==harmful` / rule-based?)

| Stage | aggregate | n | Pearson | Spearman | AUROC |
|---|---|---:|---:|---:|---:|
| before zero-shot / count_vs_harmful | 2575 | 0.018 | 0.008 | 0.503 |
| before zero-shot / rate_vs_harmful | 2575 | -0.005 | 0.005 | 0.502 |
| before zero-shot / max_vs_harmful | 2575 | 0.006 | 0.006 | 0.503 |
| before zero-shot / count_vs_rule | 2575 | 0.018 | 0.008 | 0.503 |
| before few-shot / count_vs_harmful | 2575 | 0.018 | 0.008 | 0.503 |
| before few-shot / rate_vs_harmful | 2575 | -0.005 | 0.005 | 0.502 |
| before few-shot / max_vs_harmful | 2575 | 0.006 | 0.006 | 0.503 |
| before few-shot / count_vs_rule | 2575 | 0.018 | 0.008 | 0.503 |
| after SFT / count_vs_harmful | 2575 | 0.037 | 0.080 | 0.546 |
| after SFT / rate_vs_harmful | 2575 | 0.075 | 0.079 | 0.545 |
| after SFT / max_vs_harmful | 2575 | 0.083 | 0.083 | 0.543 |
| after SFT / count_vs_rule | 2575 | 0.037 | 0.080 | 0.546 |

## Diagnostic only: synthetic holdout (can the model fit the generator?)

**Not the headline.** High holdout F1 with flat v3 transfer is the signature of fitting the synthetic generator (shared templates / pivotal-action phrasing), not of a general critic.

| Stage / set | n | n_pos | accuracy | precision | recall | F1 | tp/fp/tn/fn |
|---|---:|---:|---:|---:|---:|---:|---|
| before zero-shot | 362 | 15 | 0.942 | 0.200 | 0.133 | 0.160 | 2/8/339/13 |
| after SFT | 362 | 15 | 0.572 | 0.088 | 1.000 | 0.162 | 15/155/192/0 |
| after SFT on *train* (overfit check) | 1399 | 60 | 0.602 | 0.095 | 0.967 | 0.172 | 58/555/784/2 |

Synthetic-holdout F1: **before 0.160 → after 0.162**.

### Trajectory-level on synthetic holdout

| Stage | aggregate | n | Pearson | Spearman | AUROC |
|---|---|---:|---:|---:|---:|
| before zero-shot / count_vs_harmful | 30 | 0.000 | 0.000 | 0.500 |
| before zero-shot / count_vs_rule | 30 | 0.000 | 0.000 | 0.500 |
| after SFT / count_vs_harmful | 30 | 0.000 | 0.000 | 0.500 |
| after SFT / count_vs_rule | 30 | 0.000 | 0.000 | 0.500 |

## What this can and cannot claim

These figures are the **mock** backend, not Qwen. Treat them as a pipeline check and a qualitative template-overfit warning. Mock few-shot equals mock zero-shot (the keyword completer ignores demonstrations). After the fit, **recall of `high-unsafe` rose while precision and accuracy collapsed** (over-flagging). That is the other failure mode of fitting synthetic prefixes: shared exploration/NPC text is treated as unsafe, so both halves of a contrastive pair light up and trajectory AUROC stays near chance. Synthetic-holdout F1 staying low means the stand-in did **not** even cleanly fit the generator — do not read a v3 F1 bump (or drop) as Qwen SFT.

- **Can claim:** the conversion, task-level split, and metric code ran on the real corpora with a train/eval firewall (no v3 in train; no `instance_id` shared with the synthetic holdout).
- **Cannot claim (this run):** that a Qwen critic improved on real OAS behavior, unless `sft_actually_ran` is true and the v3 headline moves.
- Per-action v3 labels are weak supervision (~33% localization of harmful trajectories). A critic can be right about harm and still look wrong action-by-action. Prefer trajectory-level AUROC against `role` / `rule_based`.
- Synthetic pairs are *constructed*: one pivotal `high-unsafe` action per harmful half, byte-identical prefixes. Fitting that generator is easy; transferring to messy v3 traces is the actual question.

## Hardware / model search

Searched HuggingFace cache (`~/.cache/huggingface/hub`), `/models`, `$QWEN_MODEL_PATH`. No extra weights were downloaded (disk ~full; constraint: local Qwen only, no LiteLLM / cmu.litellm.ai / Ollama proxy).

Probe notes:
- no Qwen weights on disk (huggingface cache / /models)

## Exact command to rerun

From the repo root, mock/smoke (this machine):

```bash
cd safety_monitor
PYTHONPATH=. .venv/bin/python -m safety_monitor sft-qwen \
  --train ../analysis_outputs/v4_synthetic_pairs/trajectories.jsonl \
         ../analysis_outputs/v5_synthetic_pairs/trajectories.jsonl \
  --eval ../analysis_outputs/critic_training_pairs/trajectories.jsonl \
  --out-dir ../analysis_outputs/qwen_sft \
  --backend mock
```

When a local Qwen Instruct checkpoint and a CUDA GPU are available (do **not** download a new 7B if disk is tight; point at an existing dir):

```bash
cd safety_monitor
python -m venv .venv && .venv/bin/pip install -e '.[sft]'
PYTHONPATH=. .venv/bin/python -m safety_monitor sft-qwen \
  --train ../analysis_outputs/v4_synthetic_pairs/trajectories.jsonl \
         ../analysis_outputs/v5_synthetic_pairs/trajectories.jsonl \
  --eval ../analysis_outputs/critic_training_pairs/trajectories.jsonl \
  --out-dir ../analysis_outputs/qwen_sft \
  --backend hf \
  --model-path /path/to/Qwen2.5-1.5B-Instruct \
  --epochs 2
```

Optional: `--max-v3-trajectories 50` for a cheaper HF eval smoke. Full v3 eval is ~2.5k trajectories / ~24k actions.

## Files

| File | Contents |
|---|---|
| `report.md` | this document |
| `metrics.json` | before/after action + trajectory metrics |
| `split.json` | task ids for train vs synthetic holdout |
| `probe.json` | GPU / weights / backend decision |
| `example_counts.json` | SFT row counts |
| `train_stats.json` | mock fit or LoRA trainer metrics |

