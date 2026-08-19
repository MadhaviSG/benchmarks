"""Markdown report for the local Qwen SFT critic experiment."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _cell(metrics: dict[str, Any] | None, *keys: str) -> str:
    cur: Any = metrics
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return "n/a"
        cur = cur[key]
    if isinstance(cur, float):
        return f"{cur:.3f}"
    if cur is None:
        return "n/a"
    return str(cur)


def _action_row(stage: dict[str, Any], eval_name: str) -> str:
    block = (stage or {}).get(eval_name, {}).get("action", {})
    cm = block.get("confusion") or {}
    return (
        f"| {eval_name} | {_cell(block, 'n')} | {_cell(block, 'n_positive')} | "
        f"{_cell(block, 'accuracy')} | {_cell(block, 'precision_high_unsafe')} | "
        f"{_cell(block, 'recall_high_unsafe')} | {_cell(block, 'f1_high_unsafe')} | "
        f"{cm.get('tp', 'n/a')}/{cm.get('fp', 'n/a')}/{cm.get('tn', 'n/a')}/{cm.get('fn', 'n/a')} |"
    )


def _traj_row(stage: dict[str, Any], eval_name: str, prefix: str) -> str:
    block = (stage or {}).get(eval_name, {}).get("trajectory", {})
    return (
        f"| {eval_name} / {prefix} | {_cell(block, 'n_trajectories')} | "
        f"{_cell(block, f'{prefix}_pearson')} | {_cell(block, f'{prefix}_spearman')} | "
        f"{_cell(block, f'{prefix}_auroc')} |"
    )


def write_report(path: str | Path, result: dict[str, Any]) -> None:
    probe = result.get("probe") or {}
    split = result.get("split") or {}
    counts = result.get("example_counts") or {}
    metrics = result.get("metrics") or {}
    before = metrics.get("before_zero_shot") or {}
    few = metrics.get("before_few_shot") or {}
    after = metrics.get("after_sft") or {}
    ran = bool(result.get("sft_actually_ran"))
    backend = result.get("backend")

    headline = (
        "Local Qwen LoRA SFT **completed**."
        if ran
        else "Local Qwen LoRA SFT **did not run**. "
        "This machine has no GPU and no on-disk Qwen weights; the numbers below "
        "are from the CPU **mock** backend (keyword zero-shot vs hashed-ngram "
        "logistic regression) so the split and metrics pipeline is verified."
    )

    v3_before_f1 = _cell(before.get("v3_eval", {}).get("action"), "f1_high_unsafe")
    v3_after_f1 = _cell(after.get("v3_eval", {}).get("action"), "f1_high_unsafe")
    hold_before_f1 = _cell(
        before.get("synthetic_holdout", {}).get("action"), "f1_high_unsafe"
    )
    hold_after_f1 = _cell(
        after.get("synthetic_holdout", {}).get("action"), "f1_high_unsafe"
    )

    interpretation = _interpret(ran, before, after)

    lines = [
        "# Local Qwen SFT for the external safety critic",
        "",
        headline,
        "",
        "## Status",
        "",
        f"- **SFT actually ran:** `{ran}`",
        f"- **Backend used:** `{backend}`",
        f"- **GPU/CUDA:** `{probe.get('cuda')}`",
        f"- **Torch / transformers / peft:** `{probe.get('torch_available')}` / "
        f"`{probe.get('transformers_available')}` / `{probe.get('peft_available')}`",
        f"- **Local Qwen path:** `{probe.get('model_path')}`",
        f"- **Probe reason:** {probe.get('reason')}",
        "",
        "## Train/eval firewall",
        "",
        "Training uses **synthetic v4 + v5** trajectories only. Real OAS v3 runs "
        "(`analysis_outputs/critic_training_pairs`) are eval-only. The synthetic "
        "holdout is a **task-level** (`instance_id`) cut: no step from a holdout "
        "task is in train. Holdout metrics are a leakage diagnostic "
        '("can the model fit the generator?") and **must not** be treated as '
        "the headline number.",
        "",
        f"- Train tasks: **{split.get('n_train_tasks')}** "
        f"({split.get('n_train_trajectories')} trajectories)",
        f"- Synthetic holdout tasks: **{split.get('n_holdout_tasks')}** "
        f"({split.get('n_holdout_trajectories')} trajectories)",
        f"- V3 eval trajectories: **{split.get('n_v3_eval_trajectories')}** "
        f"across {split.get('n_v3_eval_tasks')} tasks",
        f"- Split salt: `{split.get('salt')}`, holdout fraction `{split.get('holdout_fraction')}`",
        f"- Train∩holdout instance_ids: `{result.get('firewall', {}).get('holdout_instance_overlap_with_train')}`",
        "",
        "## Example counts (one SFT row per labeled action)",
        "",
        "| Split | Examples | high-unsafe | low-unsafe | tasks | trajectories |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("train", "synthetic_holdout", "v3_eval"):
        block = counts.get(name) or {}
        lines.append(
            f"| {name} | {block.get('n_examples', 'n/a')} | "
            f"{block.get('n_high_unsafe', 'n/a')} | {block.get('n_low_unsafe', 'n/a')} | "
            f"{block.get('n_tasks', 'n/a')} | {block.get('n_trajectories', 'n/a')} |"
        )

    lines += [
        "",
        "## Headline: v3 eval (weak per-action labels + trajectory GT)",
        "",
        "Per-action labels on v3 are **weak supervision** (signature + sink + "
        "contrastive lift). Trajectory roles (`harmful` / `prevented` / `clean`) "
        "and `rule_based` come from the environment checkpoint and are the "
        "stronger ground truth. See `analysis_outputs/critic_training_pairs/report.md`.",
        "",
        "### Action-level (positive class = `high-unsafe`)",
        "",
        "| Stage / set | n | n_pos | accuracy | precision | recall | F1 | tp/fp/tn/fn |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
        _action_row({"v3_eval": before.get("v3_eval")}, "v3_eval").replace(
            "| v3_eval |", "| before zero-shot |"
        ),
        _action_row({"v3_eval": few.get("v3_eval")}, "v3_eval").replace(
            "| v3_eval |", "| before few-shot |"
        ),
        _action_row({"v3_eval": after.get("v3_eval")}, "v3_eval").replace(
            "| v3_eval |", "| after SFT |"
        ),
        "",
        f"Headline F1 (`high-unsafe` on v3): **before {v3_before_f1} → after {v3_after_f1}**.",
        "",
        "### Trajectory-level (does critic mass predict `role==harmful` / rule-based?)",
        "",
        "| Stage | aggregate | n | Pearson | Spearman | AUROC |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for stage_name, stage in (
        ("before zero-shot", before),
        ("before few-shot", few),
        ("after SFT", after),
    ):
        for prefix in (
            "count_vs_harmful",
            "rate_vs_harmful",
            "max_vs_harmful",
            "count_vs_rule",
        ):
            row = _traj_row(stage, "v3_eval", prefix)
            lines.append(row.replace("| v3_eval /", f"| {stage_name} /"))

    lines += [
        "",
        "## Diagnostic only: synthetic holdout (can the model fit the generator?)",
        "",
        "**Not the headline.** High holdout F1 with flat v3 transfer is the "
        "signature of fitting the synthetic generator (shared templates / "
        "pivotal-action phrasing), not of a general critic.",
        "",
        "| Stage / set | n | n_pos | accuracy | precision | recall | F1 | tp/fp/tn/fn |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
        _action_row(
            {"synthetic_holdout": before.get("synthetic_holdout")}, "synthetic_holdout"
        ).replace("| synthetic_holdout |", "| before zero-shot |"),
        _action_row(
            {"synthetic_holdout": after.get("synthetic_holdout")}, "synthetic_holdout"
        ).replace("| synthetic_holdout |", "| after SFT |"),
        _action_row(
            {"synthetic_train": after.get("synthetic_train")}, "synthetic_train"
        ).replace("| synthetic_train |", "| after SFT on *train* (overfit check) |"),
        "",
        f"Synthetic-holdout F1: **before {hold_before_f1} → after {hold_after_f1}**.",
        "",
        "### Trajectory-level on synthetic holdout",
        "",
        "| Stage | aggregate | n | Pearson | Spearman | AUROC |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for stage_name, stage in (("before zero-shot", before), ("after SFT", after)):
        for prefix in ("count_vs_harmful", "count_vs_rule"):
            row = _traj_row(stage, "synthetic_holdout", prefix)
            lines.append(row.replace("| synthetic_holdout /", f"| {stage_name} /"))

    lines += [
        "",
        "## What this can and cannot claim",
        "",
        interpretation,
        "",
        "- **Can claim:** the conversion, task-level split, and metric code ran "
        "on the real corpora with a train/eval firewall (no v3 in train; no "
        "`instance_id` shared with the synthetic holdout).",
        "- **Cannot claim (this run):** that a Qwen critic improved on real OAS "
        "behavior, unless `sft_actually_ran` is true and the v3 headline moves.",
        "- Per-action v3 labels are weak supervision (~33% localization of harmful "
        "trajectories). A critic can be right about harm and still look wrong "
        "action-by-action. Prefer trajectory-level AUROC against `role` / `rule_based`.",
        "- Synthetic pairs are *constructed*: one pivotal `high-unsafe` action per "
        "harmful half, byte-identical prefixes. Fitting that generator is easy; "
        "transferring to messy v3 traces is the actual question.",
        "",
        "## Hardware / model search",
        "",
        "Searched HuggingFace cache (`~/.cache/huggingface/hub`), `/models`, "
        "`$QWEN_MODEL_PATH`. No extra weights were downloaded (disk ~full; "
        "constraint: local Qwen only, no LiteLLM / cmu.litellm.ai / Ollama proxy).",
        "",
        "Probe notes:",
    ]
    for note in probe.get("notes") or []:
        lines.append(f"- {note}")

    lines += [
        "",
        "## Exact command to rerun",
        "",
        "From the repo root, mock/smoke (this machine):",
        "",
        "```bash",
        "cd safety_monitor",
        "PYTHONPATH=. .venv/bin/python -m safety_monitor sft-qwen \\",
        "  --train ../analysis_outputs/v4_synthetic_pairs/trajectories.jsonl \\",
        "         ../analysis_outputs/v5_synthetic_pairs/trajectories.jsonl \\",
        "  --eval ../analysis_outputs/critic_training_pairs/trajectories.jsonl \\",
        "  --out-dir ../analysis_outputs/qwen_sft \\",
        "  --backend mock",
        "```",
        "",
        "When a local Qwen Instruct checkpoint and a CUDA GPU are available "
        "(do **not** download a new 7B if disk is tight; point at an existing dir):",
        "",
        "```bash",
        "cd safety_monitor",
        "python -m venv .venv && .venv/bin/pip install -e '.[sft]'",
        "PYTHONPATH=. .venv/bin/python -m safety_monitor sft-qwen \\",
        "  --train ../analysis_outputs/v4_synthetic_pairs/trajectories.jsonl \\",
        "         ../analysis_outputs/v5_synthetic_pairs/trajectories.jsonl \\",
        "  --eval ../analysis_outputs/critic_training_pairs/trajectories.jsonl \\",
        "  --out-dir ../analysis_outputs/qwen_sft \\",
        "  --backend hf \\",
        "  --model-path /path/to/Qwen2.5-1.5B-Instruct \\",
        "  --epochs 2",
        "```",
        "",
        "Optional: `--max-v3-trajectories 50` for a cheaper HF eval smoke. "
        "Full v3 eval is ~2.5k trajectories / ~24k actions.",
        "",
        "## Files",
        "",
        "| File | Contents |",
        "|---|---|",
        "| `report.md` | this document |",
        "| `metrics.json` | before/after action + trajectory metrics |",
        "| `split.json` | task ids for train vs synthetic holdout |",
        "| `probe.json` | GPU / weights / backend decision |",
        "| `example_counts.json` | SFT row counts |",
        "| `train_stats.json` | mock fit or LoRA trainer metrics |",
        "",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _interpret(ran: bool, before: dict, after: dict) -> str:
    def f1(stage: dict, name: str) -> float | None:
        val = ((stage.get(name) or {}).get("action") or {}).get("f1_high_unsafe")
        return float(val) if isinstance(val, (int, float)) else None

    def acc(stage: dict, name: str) -> float | None:
        val = ((stage.get(name) or {}).get("action") or {}).get("accuracy")
        return float(val) if isinstance(val, (int, float)) else None

    v3_b, v3_a = f1(before, "v3_eval"), f1(after, "v3_eval")
    h_a = f1(after, "synthetic_holdout")
    v3_acc_b, v3_acc_a = acc(before, "v3_eval"), acc(after, "v3_eval")
    prefix = (
        "These figures are **Qwen LoRA**."
        if ran
        else "These figures are the **mock** backend, not Qwen. Treat them as a pipeline check and a qualitative template-overfit warning. Mock few-shot equals mock zero-shot (the keyword completer ignores demonstrations)."
    )
    if (
        h_a is not None
        and v3_a is not None
        and h_a >= 0.6
        and (v3_a - (v3_b or 0)) < 0.05
    ):
        return (
            prefix + " Synthetic holdout F1 is high while v3 is flat: that is "
            "**keyword / generator overfitting**, not a working external critic. "
            "The constructed v4/v5 pairs share templated pivotal actions; a bag-of-words "
            "or small LM can copy that style without reading real OAS traces."
        )
    if (
        v3_acc_a is not None
        and v3_acc_b is not None
        and v3_acc_a < v3_acc_b - 0.1
        and (h_a or 0) < 0.4
    ):
        return (
            prefix
            + " After the fit, **recall of `high-unsafe` rose while precision and "
            "accuracy collapsed** (over-flagging). That is the other failure mode of "
            "fitting synthetic prefixes: shared exploration/NPC text is treated as "
            "unsafe, so both halves of a contrastive pair light up and trajectory "
            "AUROC stays near chance. Synthetic-holdout F1 staying low means the "
            "stand-in did **not** even cleanly fit the generator — do not read a "
            "v3 F1 bump (or drop) as Qwen SFT."
        )
    if v3_a is not None and v3_b is not None and v3_a > v3_b + 0.05:
        return (
            prefix
            + " v3 action F1 moved up after the fit. Still treat per-action v3 labels "
            "as weak supervision and confirm the trajectory-level AUROC against "
            "`role==harmful` / rule-based score before claiming critic gains."
        )
    return (
        prefix
        + " No clear v3 transfer. Do not report the synthetic holdout as the result."
    )
