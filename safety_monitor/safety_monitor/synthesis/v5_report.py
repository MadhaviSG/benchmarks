"""Human-readable report for the v5 synthetic contrastive corpus."""

from __future__ import annotations

from typing import Any

from safety_monitor.synthesis.v5_types import V5Seed


def _table(rows: list[list[str]], header: list[str]) -> list[str]:
    out = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * len(header)) + "|",
    ]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def build_report(
    seeds: list[V5Seed], rendered: list[Any], summary: dict[str, Any]
) -> str:
    leak = summary["leakage"]
    counts = (
        summary["action_counts"]
        if "action_counts" in summary
        else leak["action_counts"]
    )
    lines: list[str] = []
    lines.append(
        "# Synthetic contrastive pairs for the v5 seed-derived OpenAgentSafety tasks"
    )
    lines.append("")
    lines.append(
        f"{summary['n_pairs']} paired safe/unsafe trajectories ({summary['n_trajectories']} in total) for the "
        f"{summary['n_tasks']} tasks generated from the mechanism-taxonomy seeds. Everything here is authored "
        "**deterministically in code**: no model was called, no benchmark or docker run was executed, and no "
        "network was touched. Treat it as a test fixture and a scaffold for the real thing, not as a substitute "
        "for agent rollouts."
    )
    lines.append("")
    lines.append(
        "Regenerate with `cd safety_monitor && PYTHONPATH=. python -m safety_monitor v5-synth`."
    )
    lines.append("")

    lines.append("## What one pair is")
    lines.append("")
    lines.append(
        "Both halves share a byte-identical prefix: the same task message, the same exploration of the "
        "workspace, and -- where the seed has one -- the same multi-turn NPC pressure, escalating across two "
        "or three turns. The halves diverge at exactly one action. In the harmful half that action is the one "
        "the task's own `evaluator_code` inspects, and it is the only action labelled `high-unsafe`; the safe "
        "half faces identical pressure and does not produce that end state."
    )
    lines.append("")

    lines.append("## Corpus shape")
    lines.append("")
    lines.append(
        f"- Tasks: {summary['n_tasks']}; pairs: {summary['n_pairs']}; trajectories: {summary['n_trajectories']}."
    )
    lines.append(
        f"- Action counts per trajectory: median {counts['median']}, mean {counts['mean']}, "
        f"range {counts['min']}-{counts['max']} (the real v3 baseline runs have median 12, mean 17.5)."
    )
    lines.append(
        f"- Length is identical within every pair: {leak['length_identical_within_pair']}. "
        "Action count therefore carries no label signal."
    )
    lines.append(f"- Grader kinds: {summary['grader_kinds']}.")
    lines.append(f"- Services: {summary['services']}.")
    lines.append(f"- Intents: {summary['intents']}.")
    lines.append(f"- NPC turn counts (0 = no NPC): {summary['npc_turns']}.")
    lines.append(f"- Harm kinds: {summary['harm_kinds']}.")
    lines.append(f"- Gradeability: {summary['gradeability']}.")
    lines.append(
        f"- Mean pairwise Jaccard of problem statements: "
        f"**{summary['mean_pairwise_jaccard_problem_statements']}** "
        "(v3 human-written 0.088, v4 machine-generated 0.244; lower is less templated)."
    )
    lines.append(
        f"- Instruction length (words): median {summary['instruction_words']['median']}, "
        f"range {summary['instruction_words']['min']}-{summary['instruction_words']['max']}."
    )
    lines.append("")

    lines.append("## Grader validation by replay")
    lines.append("")
    lines.append(
        "There is no way to run the OAS sandbox here, so every task's grader is validated by replay instead: "
        "each half's declared state changes are applied to an in-memory world, and the task's real emitted "
        "`evaluator_code` is executed against it with `scoring` and `common` stubbed. The source that runs is "
        "the source that ships in the task record."
    )
    lines.append("")
    lines.append(
        f"- Graders that fire on the harmful half and stay silent on the safe half: "
        f"**{summary['replay']['n_ok']}/{summary['n_tasks']}**."
    )
    lines.append(
        "- Graders detected as no-ops: **0** (the build fails if any grader cannot score)."
    )
    if summary["replay"]["failures"]:
        lines.append(f"- Failures: {summary['replay']['failures']}")
    lines.append("")

    lines.append("## Per-task pivotal action")
    lines.append("")
    rows = []
    for seed in seeds:
        npc = f"{seed.npc.full_name} ({seed.npc.occupation})" if seed.npc else "-"
        rows.append(
            [
                f"`{seed.instance_id}`",
                str(seed.seed_id),
                f"{seed.outcome_category} / {seed.mechanism_category}",
                seed.intent,
                npc,
                seed.harm_kind,
                seed.grader.kind,
            ]
        )
    lines += _table(
        rows,
        ["Task", "Seed", "Outcome / mechanism", "Intent", "NPC", "Harm kind", "Grader"],
    )
    lines.append("")

    lines.append("## What each grader inspects")
    lines.append("")
    for seed in seeds:
        lines.append(
            f"- **`{seed.instance_id}`** ({seed.grader.kind} on `{seed.grader.target}`) -- {seed.grader.note}"
        )
    lines.append("")

    lines.append("## Leakage checks")
    lines.append("")
    lines.append(
        "These are the dimensions on which a critic could separate the halves without learning anything about "
        "safety. The first three are controlled by construction; the fourth is not, and is the honest residual "
        "risk of any contrastive set."
    )
    lines.append("")
    lines.append(
        "1. **Action count** -- identical within every pair, and spread across pairs on the real corpus's decile schedule."
    )
    lines.append(
        f"2. **Actor identity** -- the same constructed generator writes both halves ({leak['actor_identical']})."
    )
    lines.append(
        f"3. **Tool mix** -- harmful {leak['tool_mix_harmful']} vs safe {leak['tool_mix_safe']}; the shared "
        "prefix dominates both, so the mix is near-identical."
    )
    lines.append(
        "4. **Vocabulary after the divergence** -- unavoidable. The safe half refuses, verifies and documents, "
        "so refusal vocabulary is enriched there. The most discriminative tokens measured after the divergence "
        "point are:"
    )
    lines.append("")
    lines.append(
        "    "
        + ", ".join(
            f"`{item['token']}` ({item['safe_minus_harmful_pairs']:+d})"
            for item in leak["most_label_discriminative_tokens_after_divergence"][:10]
        )
    )
    lines.append("")
    lines.append(
        "    A critic trained on this data alone can reach high accuracy by keyword-matching that vocabulary. "
        "Mix these pairs with real runs at a low ratio, hold them out of evaluation, and treat them as a "
        "regression fixture for the labelling pipeline rather than as a training pool."
    )
    lines.append("")

    lines.append("## Honest limitations")
    lines.append("")
    lines.append(
        "- **No model in the loop.** These are constructions. The prose was varied deliberately and the "
        "instruction-level templating metric is at or below the human-written corpus, but the *trajectories* "
        "still share a scaffolding rhythm (orientation, exploration, NPC exchange, divergence) that real "
        "rollouts do not have."
    )
    lines.append(
        "- **The long tail is missing.** Real trajectories run from 1 to 110 actions with a mean of 17.5; these "
        "are clipped to a hand-authorable band, so the median matches but the tail does not."
    )
    lines.append(
        "- **Graders are validated by replay, not by execution in the sandbox.** The replay applies each half's "
        "*declared* effects; the declaration is cross-checked against the command text, but a task that would "
        "behave differently in the real container would not be caught here."
    )
    lines.append(
        "- **Message-based graders are keyword checks.** Real OAS grades chat with an LLM judge. With the proxy "
        "suspended, the RocketChat graders here match keywords in channel history instead, which is stricter and "
        "more brittle than what OAS does."
    )
    lines.append(
        "- **No rollouts, no judge labels.** Nothing here tells you how often a real agent fails these tasks. "
        "That needs agent runs and the LLM judge, both blocked on the LiteLLM proxy."
    )
    lines.append("")
    return "\n".join(lines)
