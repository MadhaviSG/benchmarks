"""Independent verification tests for v6 synthetic corpus leakage gates."""

from __future__ import annotations

from pathlib import Path

import pytest
from safety_monitor.synthesis.v6_leakage_verification import (
    verify_stats_json,
    verify_v6_corpus,
)
from safety_monitor.synthesis.v6_validate import LEAKAGE_TOKEN_MAX_RATIO


def test_v6_post_divergence_token_skew_under_threshold():
    result = verify_v6_corpus(grid_fill=0, max_ratio=LEAKAGE_TOKEN_MAX_RATIO)
    assert result.ok, result.reasons
    assert result.max_skew_ratio <= LEAKAGE_TOKEN_MAX_RATIO


def test_v6_no_legacy_opaque_marker_prefixes():
    result = verify_v6_corpus(grid_fill=0)
    assert result.legacy_marker_hits == 0
    assert result.mk_marker_hits > 0


def test_v6_stats_json_leakage_gate(stats_path: Path | None = None):
    path = stats_path or (
        Path(__file__).resolve().parents[2]
        / "analysis_outputs"
        / "v6_synthetic_pairs"
        / "stats.json"
    )
    if not path.exists():
        pytest.skip("v6 stats.json not rendered yet")
    result = verify_stats_json(path, max_ratio=LEAKAGE_TOKEN_MAX_RATIO)
    assert result.ok, result.reasons


def test_printf_rm_skew_balanced():
    result = verify_v6_corpus(grid_fill=0)
    by_token = {
        row["token"]: row["safe_minus_harmful_pairs"] for row in result.top_tokens
    }
    n = result.n_pairs
    for token in ("printf", "rm"):
        if token in by_token:
            ratio = abs(by_token[token]) / n
            assert ratio <= LEAKAGE_TOKEN_MAX_RATIO, (
                f"{token} skew {ratio:.3f} > {LEAKAGE_TOKEN_MAX_RATIO}"
            )
