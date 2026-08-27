# ShieldGemma SFT setup (GPU day)

Fine-tune `google/shieldgemma-2b` with LoRA on nested subsets of the synthetic
corpus and evaluate every rung on the real v3 trajectories. Core
`safety_monitor` stays GPU-free; GPU libraries are an extra.

## 1. One-time gated weight download

ShieldGemma-2B is gated. Accept the license on
[https://huggingface.co/google/shieldgemma-2b](https://huggingface.co/google/shieldgemma-2b)
with an HF account, then:

```bash
export HF_TOKEN=hf_...   # token that can read gated repos
python - <<'PY'
from huggingface_hub import snapshot_download
path = snapshot_download(
    "google/shieldgemma-2b",
    token=True,
    local_dir="/models/shieldgemma-2b",
    local_dir_use_symlinks=False,
)
print("downloaded to", path)
PY
export SHIELDGEMMA_MODEL_PATH=/models/shieldgemma-2b
```

The critic **never downloads**. `--model-path` overrides the env var. Both must
point at a local directory that already contains `config.json` and weights.

## 2. Eval-file placement (do not commit)

Mined v3 trajectories embed sandbox GitLab tokens. They are gitignored.

```bash
# Expected path inside the git repo (benchmarks/):
ls -l analysis_outputs/critic_training_pairs/trajectories.jsonl
# Should report ~2575 JSONL rows. Do not synthesize a substitute.
```

Training data (committed): `analysis_outputs/synthetic_pairs/trajectories.jsonl`
(1,250 trajectories, 625 tasks, shipped `split` in {train,dev,test}).

## 3. Install

From the **benchmarks repo root** (`.../benchmarks`):

```bash
# CPU / mock (already enough for pipeline tests)
cd safety_monitor
PYTHONPATH=. python -m pip install -e .

# GPU day — torch, transformers, peft, accelerate, matplotlib, sentencepiece
PYTHONPATH=. python -m pip install -e '.[sft]'
# equivalent: python -m pip install -e '.[gpu]'
```

Use the existing repo venv if present:

```bash
cd /path/to/benchmarks
source .venv/bin/activate
cd safety_monitor
PYTHONPATH=. python -m pip install -e '.[sft]'
```

## 4. Smoke test (first command on the GPU server)

Zero-shot only, first 100 v3 trajectories, prints a Yes-probability summary.
Fails loudly if weights, eval file, or CUDA are missing (`--backend auto`/`hf`).

```bash
cd safety_monitor
export SHIELDGEMMA_MODEL_PATH=/models/shieldgemma-2b
PYTHONPATH=. python -m safety_monitor sft-scaling \
  --smoke \
  --backend auto \
  --out-dir /tmp/shieldgemma_smoke
```

CPU stand-in (no GPU required):

```bash
cd safety_monitor
PYTHONPATH=. python -m safety_monitor sft-scaling \
  --smoke \
  --backend mock \
  --out-dir /tmp/shieldgemma_smoke_mock
```

## 5. Full nested ladder

Rungs `0 ⊂ 200 ⊂ 400 ⊂ all-train`. Thresholds are fit on synthetic **dev** and
frozen. Headline metrics are v3 trajectory AUROC (max/count/rate vs
`role==harmful` and `rule_based`). Synthetic **test** is the generator-overfit
diagnostic.

```bash
cd safety_monitor
export SHIELDGEMMA_MODEL_PATH=/models/shieldgemma-2b
PYTHONPATH=. python -m safety_monitor sft-scaling \
  --backend hf \
  --train ../analysis_outputs/synthetic_pairs/trajectories.jsonl \
  --eval ../analysis_outputs/critic_training_pairs/trajectories.jsonl \
  --out-dir ../analysis_outputs/shieldgemma_sft_scaling \
  --rungs 0,200,400,all \
  --epochs 2 \
  --seed 42
```

Mock ladder (CPU, verifies orchestration + report + plot):

```bash
cd safety_monitor
PYTHONPATH=. python -m safety_monitor sft-scaling \
  --backend mock \
  --out-dir /tmp/ladder_smoke
```

## 6. Single-run SFT (Qwen path)

`sft-qwen` was documented but never registered; `sft-run` is the command, with
`sft-qwen` as an alias. Default split is the hashed `qwen-sft` cut; pass
`--use-shipped-splits` to use the corpus train/dev/test field instead.

```bash
cd safety_monitor
PYTHONPATH=. python -m safety_monitor sft-run \
  --backend mock \
  --out-dir /tmp/sft_run_mock
```

## 7. Outputs

| Path | Contents |
|---|---|
| `results.json` | Combined rungs, firewall (`train ∩ v3-eval instance_ids == []`) |
| `report.md` | Rung × metric table |
| `auroc_vs_n_train.png` | v3 + synthetic-test AUROC vs n_train (log-x) |
| `rung_<n>/` | Per-rung metrics, chosen threshold, LoRA adapter |

Do not commit eval JSONL, weights, or `results_safety_comparison/`.
