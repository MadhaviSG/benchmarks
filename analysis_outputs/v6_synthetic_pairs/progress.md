# v6 progress

**Updated:** 2026-08-20 (Phase 0 re-check + bootstrap continuation)

## Phase 0 (this run)

| Check | Status |
|-------|--------|
| Disk (`/`) | **98% used** (~5.9G free) — tight for large renders |
| GPU (`nvidia-smi`) | **Not available** on this host (no NVIDIA device) |
| LiteLLM / `gpt-5mini.json` | **Blocked** — `https://cmu.litellm.ai` returns HTML **Service Suspended** (`ServiceUnavailableError` via `benchmarks/.venv` + `litellm`) |
| `OPENAI_API_KEY` / `LITELLM_API_BASE` env | **Not set** in shell |
| Alternate configs | All CMU configs same suspension; `example.json` → `llm-proxy.eval.all-hands.dev` has placeholder key only |
| LLM drafting (`v6-author`) | **Not run** (proxy down) |

## Corpus counts

- **v6 tasks:** 550 (1100 trajectories)
- **synthetic_pairs_v2 merge:** 625 pairs / 1250 trajectories (v4+v5+v6)
- Mean pairwise Jaccard (v6 problem statements): **0.4277**
- Grid cells filled: **see `stats.json`** (bootstrap grid-fill 200)
- Last bootstrap validation: 603 drafted → **550 accepted**, 53 rejected (`novelty` only)

### Note on prior 47-task snapshot

Earlier shipped state (**47 tasks / 94 trajectories**) lived in `pairs.jsonl` but **`accepted_seeds.jsonl` was absent**, so a `--grid-fill 40` re-render replaced the corpus with **16** tasks before a larger `--grid-fill 200` bootstrap rebuild. **Recovered scale via deterministic bootstrap only** (not LLM). Consider exporting accepted seeds after future LLM runs.

## Remaining to plan target

- Plan: **500 total pairs** (75 v4+v5 + **425 v6**). Current v6 **550** → **overshoot +125** vs v6-only target (merge totals **625**).

## Blockers

1. **CMU LiteLLM proxy suspended** — need working `base_url` + key (env or new `.llm_config` JSON).
2. **No GPU on this machine** — SFT not attempted (per instructions).
3. **Disk space** — monitor before large artifact writes.

## Resume

**When LiteLLM works** (use venv Python):

```bash
cd benchmarks/safety_monitor && PYTHONPATH=.. ../.venv/bin/python -m safety_monitor v6-author \
  --priority --grid-fill 20 --batch-size 20 --target 425 \
  --config ../.llm_config/gpt-5mini.json
# then re-render if author only appends seeds:
PYTHONPATH=. ../.venv/bin/python -m safety_monitor v6-synth --merge
```

**Bootstrap-only** (trim grid-fill if corpus already large):

```bash
cd benchmarks/safety_monitor && PYTHONPATH=. python3 -m safety_monitor v6-synth --grid-fill 40 --merge
```

**Minimal LiteLLM smoke test** (same path as `v6_author.call_llm`):

```bash
cd benchmarks/safety_monitor && PYTHONPATH=. ../.venv/bin/python -c "
from pathlib import Path
from safety_monitor.synthesis.v6_author import load_llm_config, call_llm
cfg = load_llm_config(Path('../.llm_config/gpt-5mini.json'))
print(call_llm([{'role':'user','content':'Reply exactly: ok'}], cfg)[:80])
"
```
