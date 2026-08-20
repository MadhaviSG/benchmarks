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
from safety_monitor.synthesis.v6_generate import load_seeds, schedule
from safety_monitor.synthesis.v6_validate import LEAKAGE_TOKEN_MAX_RATIO


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


def verify_rendered_pairs(
    rendered: list[RenderedPair],
    *,
    max_ratio: float = LEAKAGE_TOKEN_MAX_RATIO,
) -> LeakageVerification:
    report = token_skew_report(rendered)
    n = int(report["n_pairs"])
    top = report["most_label_discriminative_tokens_after_divergence"]
    reasons: list[str] = []
    max_delta = 0
    max_token = ""
    if n:
        for row in top:
            delta = abs(int(row["safe_minus_harmful_pairs"]))
            if delta > max_delta:
                max_delta = delta
                max_token = str(row["token"])
    max_ratio_observed = max_delta / n if n else 0.0
    if max_ratio_observed > max_ratio:
        offenders = [
            row
            for row in top
            if abs(int(row["safe_minus_harmful_pairs"])) / n > max_ratio
        ]
        reasons.append(
            f"token skew {max_ratio_observed:.3f} > {max_ratio}: {offenders[:5]}"
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
        ok=not reasons,
        reasons=reasons,
    )


def verify_v6_corpus(
    *,
    grid_fill: int = 0,
    max_ratio: float = LEAKAGE_TOKEN_MAX_RATIO,
) -> LeakageVerification:
    seeds = load_seeds(
        include_bootstrap=True, include_content=True, grid_fill=grid_fill
    )
    plan = schedule(seeds)
    rendered = [
        build_pair(seed, *plan[seed.instance_id])
        for seed in sorted(seeds, key=lambda s: s.instance_id)
    ]
    return verify_rendered_pairs(rendered, max_ratio=max_ratio)


def verify_stats_json(
    stats_path: Path,
    *,
    max_ratio: float = LEAKAGE_TOKEN_MAX_RATIO,
) -> LeakageVerification:
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    leakage = stats.get("leakage") or {}
    n = int(leakage.get("n_pairs") or 0)
    top = leakage.get("most_label_discriminative_tokens_after_divergence") or []
    max_delta = 0
    max_token = ""
    for row in top:
        delta = abs(int(row["safe_minus_harmful_pairs"]))
        if delta > max_delta:
            max_delta = delta
            max_token = str(row["token"])
    max_ratio_observed = max_delta / n if n else 0.0
    reasons: list[str] = []
    if max_ratio_observed > max_ratio:
        reasons.append(f"stats.json skew {max_ratio_observed:.3f} > {max_ratio}")
    legacy_path = Path(stats.get("outputs", {}).get("legacy", ""))
    legacy_hits = 0
    mk_hits = 0
    if legacy_path.exists():
        text = legacy_path.read_text(encoding="utf-8")
        legacy_hits, mk_hits = scan_trajectory_text(text)
        if legacy_hits:
            reasons.append(f"legacy file has {legacy_hits} sm_/hm_ markers")
    return LeakageVerification(
        n_pairs=n,
        max_skew_ratio=max_ratio_observed,
        max_skew_token=max_token,
        max_skew_delta=max_delta,
        top_tokens=top,
        legacy_marker_hits=legacy_hits,
        mk_marker_hits=mk_hits,
        ok=not reasons,
        reasons=reasons,
    )
