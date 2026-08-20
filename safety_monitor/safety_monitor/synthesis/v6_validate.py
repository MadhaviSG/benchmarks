"""Hard validation gates for v6 drafted seeds."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from safety_monitor.synthesis.v5_generate import (
    build_pair,
    leakage_report,
    mean_pairwise_jaccard,
    replay_grader,
    schedule as _v5_schedule,
)
from safety_monitor.synthesis.v5_graders import is_no_op, synthesize_evaluator
from safety_monitor.synthesis.v5_triage import COVERED, GENERATED, triage_by_seed
from safety_monitor.synthesis.v5_types import V5Seed
from safety_monitor.synthesis.v6_types import validate_v6_seed


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_V3 = _REPO_ROOT / "analysis_outputs" / "hf_cache" / "v3_train.jsonl"
DEFAULT_V4 = _REPO_ROOT / "analysis_outputs" / "hf_cache" / "v4_train.jsonl"
DEFAULT_V5_TASKS = (
    _REPO_ROOT / "analysis_outputs" / "v5_generated_tasks" / "v5_train.jsonl"
)
DEFAULT_V6_TASKS = (
    _REPO_ROOT / "analysis_outputs" / "v6_generated_tasks" / "v6_train.jsonl"
)

NOVELTY_MAX = 0.35
CORPUS_JACCARD_MAX = 0.15
LEAKAGE_TOKEN_MAX_RATIO = 0.20
OPENING_PREFIX_MAX_SHARE = 5
OPENING_PREFIX_LEN = 60


@dataclass
class GateResult:
    ok: bool
    gate: str
    reason: str = ""


@dataclass
class ValidationReport:
    seed: V5Seed
    accepted: bool
    gates: list[GateResult] = field(default_factory=list)

    @property
    def rejection_gate(self) -> str | None:
        for gate in self.gates:
            if not gate.ok:
                return gate.gate
        return None


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 0.0
    union = ta | tb
    return len(ta & tb) / len(union) if union else 0.0


def _load_problem_statements(*paths: Path) -> list[str]:
    texts: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                ps = record.get("problem_statement") or record.get("instruction") or ""
                if ps:
                    texts.append(str(ps))
    return texts


def gate_structural(seed: V5Seed) -> GateResult:
    problems = validate_v6_seed(seed)
    if problems:
        return GateResult(False, "structural", problems[0])
    return GateResult(True, "structural")


def gate_grader(seed: V5Seed) -> GateResult:
    code = synthesize_evaluator(seed)
    if is_no_op(code):
        return GateResult(False, "grader", "synthesised grader is a no-op")
    plan = _v5_schedule([seed])
    variant, target = plan[seed.instance_id]
    rp = build_pair(seed, variant, target)
    replay = replay_grader(seed, rp, code)
    if not replay["ok"]:
        return GateResult(
            False,
            "grader_replay",
            f"unsafe={replay['unsafe_score']} safe={replay['safe_score']} errors={replay['errors']}",
        )
    return GateResult(True, "grader_replay")


def gate_novelty(
    seed: V5Seed, existing: list[str], *, max_jaccard: float = NOVELTY_MAX
) -> GateResult:
    if not existing:
        return GateResult(True, "novelty")
    worst = max(
        jaccard(seed.seed_sentence + " " + seed.title, text) for text in existing
    )
    if worst >= max_jaccard:
        return GateResult(False, "novelty", f"max Jaccard {worst:.3f} >= {max_jaccard}")
    return GateResult(True, "novelty", f"max Jaccard {worst:.3f}")


def gate_triage(seed: V5Seed) -> GateResult:
    table = triage_by_seed()
    row = table.get(seed.seed_id)
    if row is None:
        return GateResult(True, "triage", "seed_id not in taxonomy table")
    if row.status == COVERED:
        return GateResult(False, "triage", f"already_covered by {row.matched_task}")
    if (
        row.status != GENERATED
        and row.instance_id
        and row.instance_id != seed.instance_id.replace("v6", "v5")
    ):
        # skipped seeds are allowed when explicitly recovered (different instance id)
        if row.status == "skipped":
            return GateResult(
                True, "triage", "skipped taxonomy seed explicitly recovered"
            )
        return GateResult(False, "triage", f"taxonomy status={row.status}")
    return GateResult(True, "triage")


def gate_corpus_jaccard(
    problem_statements: list[str], *, max_jaccard: float = CORPUS_JACCARD_MAX
) -> GateResult:
    if len(problem_statements) < 2:
        return GateResult(True, "corpus_jaccard", "fewer than 2 statements")
    score = mean_pairwise_jaccard(problem_statements)
    if score > max_jaccard:
        return GateResult(
            False,
            "corpus_jaccard",
            f"mean Jaccard {score:.4f} > {max_jaccard}",
        )
    return GateResult(True, "corpus_jaccard", f"mean Jaccard {score:.4f}")


def gate_opening_prefix(
    problem_statements: list[str],
    *,
    max_share: int = OPENING_PREFIX_MAX_SHARE,
    prefix_len: int = OPENING_PREFIX_LEN,
) -> GateResult:
    from collections import Counter

    prefixes = Counter(ps[:prefix_len] for ps in problem_statements)
    if not prefixes:
        return GateResult(True, "opening_prefix")
    prefix, count = prefixes.most_common(1)[0]
    if count > max_share:
        return GateResult(
            False,
            "opening_prefix",
            f"opening prefix shared by {count} tasks (> {max_share})",
        )
    return GateResult(True, "opening_prefix", f"max shared opening count {count}")


def gate_leakage(
    rendered: list[Any],
    *,
    max_ratio: float = LEAKAGE_TOKEN_MAX_RATIO,
) -> GateResult:
    report = leakage_report(rendered)
    n = int(report["n_pairs"])
    if n == 0:
        return GateResult(True, "leakage")
    worst = max(
        abs(row["safe_minus_harmful_pairs"])
        for row in report["most_label_discriminative_tokens_after_divergence"]
    )
    ratio = worst / n
    if ratio > max_ratio:
        offenders = [
            row
            for row in report["most_label_discriminative_tokens_after_divergence"]
            if abs(row["safe_minus_harmful_pairs"]) / n > max_ratio
        ]
        return GateResult(
            False,
            "leakage",
            f"post-divergence token skew {ratio:.3f} > {max_ratio}: {offenders[:3]}",
        )
    return GateResult(
        True, "leakage", f"max post-divergence token skew ratio {ratio:.3f}"
    )


def validate_corpus_gates(
    *,
    problem_statements: list[str],
    rendered: list[Any],
) -> list[GateResult]:
    return [
        gate_corpus_jaccard(problem_statements),
        gate_opening_prefix(problem_statements),
        gate_leakage(rendered),
    ]


def validate_seed_gates(
    seed: V5Seed,
    *,
    existing_problems: list[str] | None = None,
    skip_gates: set[str] | None = None,
) -> ValidationReport:
    skip = skip_gates or set()
    gates: list[GateResult] = []
    if "structural" not in skip:
        gates.append(gate_structural(seed))
    if gates and not gates[-1].ok:
        return ValidationReport(seed, False, gates)
    if "grader_replay" not in skip:
        gates.append(gate_grader(seed))
    if gates and not gates[-1].ok:
        return ValidationReport(seed, False, gates)
    if "novelty" not in skip:
        gates.append(gate_novelty(seed, existing_problems or []))
    if gates and not gates[-1].ok:
        return ValidationReport(seed, False, gates)
    if "triage" not in skip:
        gates.append(gate_triage(seed))
    accepted = all(g.ok for g in gates)
    return ValidationReport(seed, accepted, gates)


def load_existing_corpus_problems(*, include_v6: bool = False) -> list[str]:
    paths = [DEFAULT_V3, DEFAULT_V4, DEFAULT_V5_TASKS]
    if include_v6:
        paths.append(DEFAULT_V6_TASKS)
    return _load_problem_statements(*paths)


def summarise_reports(reports: list[ValidationReport]) -> dict[str, Any]:
    rejected: dict[str, int] = {}
    accepted = [r for r in reports if r.accepted]
    for report in reports:
        if not report.accepted:
            gate = report.rejection_gate or "unknown"
            rejected[gate] = rejected.get(gate, 0) + 1
    return {
        "n_drafted": len(reports),
        "n_accepted": len(accepted),
        "n_rejected": len(reports) - len(accepted),
        "rejection_by_gate": dict(sorted(rejected.items())),
        "acceptance_rate": round(len(accepted) / len(reports), 3) if reports else 0.0,
    }
