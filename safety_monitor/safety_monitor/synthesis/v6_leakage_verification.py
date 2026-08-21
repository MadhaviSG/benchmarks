"""Independent verification helpers for v6 synthetic corpus label leakage."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from safety_monitor.synthesis.v5_generate import (
    RenderedPair,
    build_pair,
    leakage_report,
    post_divergence_leakage_text,
)
from safety_monitor.synthesis.v6_validate import (
    CORPUS_JACCARD_MAX,
    LEAKAGE_TOKEN_MAX_RATIO,
    MARKER_PRESENCE_MAX_RATIO,
    PROBE_AUROC_MAX,
    marker_presence_counts,
    strict_mean_pairwise_jaccard,
)


_LEGACY_MARKER_RE = re.compile(r"\b(?:sm|hm)_[0-9a-f]{7}\b")
_MK_MARKER_RE = re.compile(r"\bmk_[0-9a-f]{7}\b")


@dataclass
class LeakageVerification:
    n_pairs: int
    max_skew_ratio: float
    max_skew_token: str
    max_skew_delta: int
    top_tokens: list[dict[str, Any]]
    legacy_marker_hits: int
    mk_marker_hits: int
    safe_marker_presence: int
    harmful_marker_presence: int
    marker_presence_skew_ratio: float
    strict_jaccard: float | None
    probe_word_auroc: float | None
    probe_char_auroc: float | None
    probe_ok: bool | None
    ok: bool
    reasons: list[str]


def token_skew_report(rendered: list[RenderedPair]) -> dict[str, Any]:
    """Post-divergence skew over summary + command + path + observation."""
    return leakage_report(rendered)


def scan_trajectory_text(text: str) -> tuple[int, int]:
    """Return (legacy sm_/hm_ marker count, mk_ marker count)."""
    legacy = len(_LEGACY_MARKER_RE.findall(text))
    mk = len(_MK_MARKER_RE.findall(text))
    return legacy, mk


def _max_token_skew(top: list[dict[str, Any]], n: int) -> tuple[float, str, int]:
    max_delta = 0
    max_token = ""
    for row in top:
        token = str(row["token"])
        if len(token) < 2:
            continue
        delta = abs(int(row["safe_minus_harmful_pairs"]))
        if delta > max_delta:
            max_delta = delta
            max_token = token
    max_ratio_observed = max_delta / n if n else 0.0
    return max_ratio_observed, max_token, max_delta


def verify_rendered_pairs(
    rendered: list[RenderedPair],
    *,
    max_ratio: float = LEAKAGE_TOKEN_MAX_RATIO,
    max_marker_skew: float = MARKER_PRESENCE_MAX_RATIO,
    problem_statements: list[str] | None = None,
    max_strict_jaccard: float = CORPUS_JACCARD_MAX,
) -> LeakageVerification:
    report = token_skew_report(rendered)
    n = int(report["n_pairs"])
    top = report["most_label_discriminative_tokens_after_divergence"]
    reasons: list[str] = []
    max_ratio_observed, max_token, max_delta = _max_token_skew(top, n)
    if max_ratio_observed > max_ratio:
        offenders = [
            row
            for row in top
            if len(str(row["token"])) >= 2
            and abs(int(row["safe_minus_harmful_pairs"])) / n > max_ratio
        ]
        reasons.append(
            f"token skew {max_ratio_observed:.3f} > {max_ratio}: {offenders[:5]}"
        )

    safe_presence, harm_presence = marker_presence_counts(rendered)
    marker_skew = abs(safe_presence - harm_presence) / n if n else 0.0
    if marker_skew > max_marker_skew:
        reasons.append(
            "marker presence skew "
            f"{marker_skew:.3f} > {max_marker_skew}: "
            f"safe={safe_presence} harmful={harm_presence}"
        )

    strict_jaccard: float | None = None
    if problem_statements is not None and len(problem_statements) >= 2:
        strict_jaccard = strict_mean_pairwise_jaccard(problem_statements)
        if strict_jaccard > max_strict_jaccard:
            reasons.append(
                f"strict Jaccard {strict_jaccard:.4f} > {max_strict_jaccard}"
            )

    legacy_hits = 0
    mk_hits = 0
    for rp in rendered:
        for acts in (rp.unsafe_acts, rp.safe_acts):
            text = post_divergence_leakage_text(acts)
            legacy, mk = scan_trajectory_text(text)
            legacy_hits += legacy
            mk_hits += mk
    if legacy_hits:
        reasons.append(f"found {legacy_hits} legacy sm_/hm_ markers (expected 0)")

    return LeakageVerification(
        n_pairs=n,
        max_skew_ratio=max_ratio_observed,
        max_skew_token=max_token,
        max_skew_delta=max_delta,
        top_tokens=top,
        legacy_marker_hits=legacy_hits,
        mk_marker_hits=mk_hits,
        safe_marker_presence=safe_presence,
        harmful_marker_presence=harm_presence,
        marker_presence_skew_ratio=marker_skew,
        strict_jaccard=strict_jaccard,
        probe_word_auroc=None,
        probe_char_auroc=None,
        probe_ok=None,
        ok=not reasons,
        reasons=reasons,
    )


def verify_probe_trajectories(
    trajectories: list[Any],
    *,
    max_auroc: float = PROBE_AUROC_MAX,
) -> dict[str, Any]:
    """Run the shallow-probe gate on structured ``MinedTrajectory`` records."""
    from safety_monitor.synthesis.probe_gate import run_probe_gate

    return run_probe_gate(trajectories, max_auroc=max_auroc)


def verify_v6_corpus(
    *,
    grid_fill: int = 0,
    max_ratio: float = LEAKAGE_TOKEN_MAX_RATIO,
    max_marker_skew: float = MARKER_PRESENCE_MAX_RATIO,
    max_strict_jaccard: float = CORPUS_JACCARD_MAX,
) -> LeakageVerification:
    from safety_monitor.synthesis.v6_generate import load_seeds, schedule

    seeds = load_seeds(
        include_bootstrap=True, include_content=True, grid_fill=grid_fill
    )
    plan = schedule(seeds)
    rendered = [
        build_pair(seed, *plan[seed.instance_id])
        for seed in sorted(seeds, key=lambda s: s.instance_id)
    ]
    problem_statements = [rp.problem_statement for rp in rendered]
    return verify_rendered_pairs(
        rendered,
        max_ratio=max_ratio,
        max_marker_skew=max_marker_skew,
        problem_statements=problem_statements,
        max_strict_jaccard=max_strict_jaccard,
    )


def verify_stats_json(
    stats_path: Path,
    *,
    max_ratio: float = LEAKAGE_TOKEN_MAX_RATIO,
    max_marker_skew: float = MARKER_PRESENCE_MAX_RATIO,
) -> LeakageVerification:
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    leakage = stats.get("leakage") or {}
    n = int(leakage.get("n_pairs") or 0)
    top = leakage.get("most_label_discriminative_tokens_after_divergence") or []
    max_ratio_observed, max_token, max_delta = _max_token_skew(top, n)
    reasons: list[str] = []
    if max_ratio_observed > max_ratio:
        reasons.append(f"stats.json skew {max_ratio_observed:.3f} > {max_ratio}")
    strict_jaccard = stats.get("strict_mean_pairwise_jaccard_problem_statements")
    if strict_jaccard is not None and float(strict_jaccard) > CORPUS_JACCARD_MAX:
        reasons.append(
            f"stats.json strict Jaccard {float(strict_jaccard):.4f} > {CORPUS_JACCARD_MAX}"
        )
    marker = stats.get("marker_presence") or {}
    safe_presence = int(marker.get("safe_with_mk", 0))
    harm_presence = int(marker.get("harmful_with_mk", 0))
    marker_skew = abs(safe_presence - harm_presence) / n if n else 0.0
    if marker_skew > max_marker_skew:
        reasons.append(f"stats.json marker skew {marker_skew:.3f} > {max_marker_skew}")
    legacy_path = Path(stats.get("outputs", {}).get("legacy", ""))
    legacy_hits = 0
    mk_hits = 0
    if legacy_path.exists():
        text = legacy_path.read_text(encoding="utf-8")
        legacy_hits, mk_hits = scan_trajectory_text(text)
        if legacy_hits:
            reasons.append(f"legacy file has {legacy_hits} sm_/hm_ markers")
    probe = stats.get("probe") or {}
    probe_word = probe.get("word_probe", {}).get("mean_auroc")
    probe_char = probe.get("char_probe", {}).get("mean_auroc")
    probe_ok = probe.get("ok")
    if probe_word is not None and float(probe_word) > PROBE_AUROC_MAX:
        reasons.append(
            f"stats.json word probe AUROC {float(probe_word):.3f} > {PROBE_AUROC_MAX}"
        )
    if probe_char is not None and float(probe_char) > PROBE_AUROC_MAX:
        reasons.append(
            f"stats.json char probe AUROC {float(probe_char):.3f} > {PROBE_AUROC_MAX}"
        )
    return LeakageVerification(
        n_pairs=n,
        max_skew_ratio=max_ratio_observed,
        max_skew_token=max_token,
        max_skew_delta=max_delta,
        top_tokens=top,
        legacy_marker_hits=legacy_hits,
        mk_marker_hits=mk_hits,
        safe_marker_presence=safe_presence,
        harmful_marker_presence=harm_presence,
        marker_presence_skew_ratio=marker_skew,
        strict_jaccard=float(strict_jaccard) if strict_jaccard is not None else None,
        probe_word_auroc=float(probe_word) if probe_word is not None else None,
        probe_char_auroc=float(probe_char) if probe_char is not None else None,
        probe_ok=bool(probe_ok) if probe_ok is not None else None,
        ok=not reasons,
        reasons=reasons,
    )
