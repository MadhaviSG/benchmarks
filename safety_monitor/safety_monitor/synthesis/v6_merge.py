"""Merge v4 + v5 + v6 synthetic corpora into combined analysis_outputs."""

from __future__ import annotations

import json
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
V1 = _REPO_ROOT / "analysis_outputs" / "synthetic_pairs"
V2 = _REPO_ROOT / "analysis_outputs" / "synthetic_pairs_v2"

SOURCES = {
    "v4": {
        "trajectories": _REPO_ROOT
        / "analysis_outputs"
        / "v4_synthetic_pairs"
        / "trajectories.jsonl",
        "pairs": _REPO_ROOT / "analysis_outputs" / "v4_synthetic_pairs" / "pairs.jsonl",
        "legacy": _REPO_ROOT
        / "analysis_outputs"
        / "v4_synthetic_pairs"
        / "safety_trajectories_v4_synthetic.jsonl",
    },
    "v5": {
        "trajectories": _REPO_ROOT
        / "analysis_outputs"
        / "v5_synthetic_pairs"
        / "trajectories.jsonl",
        "pairs": _REPO_ROOT / "analysis_outputs" / "v5_synthetic_pairs" / "pairs.jsonl",
        "legacy": _REPO_ROOT
        / "analysis_outputs"
        / "v5_synthetic_pairs"
        / "safety_trajectories_v5_synthetic.jsonl",
    },
    "v6": {
        "trajectories": _REPO_ROOT
        / "analysis_outputs"
        / "v6_synthetic_pairs"
        / "trajectories.jsonl",
        "pairs": _REPO_ROOT / "analysis_outputs" / "v6_synthetic_pairs" / "pairs.jsonl",
        "legacy": _REPO_ROOT
        / "analysis_outputs"
        / "v6_synthetic_pairs"
        / "safety_trajectories_v6_synthetic.jsonl",
    },
}


def _merge_jsonl(paths: list[tuple[str, Path]], out: Path) -> dict[str, int]:
    out.parent.mkdir(parents=True, exist_ok=True)
    per_corpus: dict[str, int] = {}
    with out.open("w", encoding="utf-8") as sink:
        for corpus, path in paths:
            if not path.exists():
                continue
            n = 0
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    record.setdefault("corpus", corpus)
                    sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                    n += 1
            per_corpus[corpus] = n
    return per_corpus


def _readme(counts: dict[str, dict[str, int]]) -> str:
    traj = counts["trajectories"]
    legacy = counts["legacy"]
    pairs = counts["pairs"]
    total_traj = sum(traj.values())
    total_legacy = sum(legacy.values())
    total_pairs = sum(pairs.values())
    lines = [
        "# Combined synthetic pairs (v4 + v5 + v6)",
        "",
        "Concatenation of v4, v5, and v6 synthetic contrast-pair corpora. Source directories "
        "under `analysis_outputs/v4_synthetic_pairs/`, `v5_synthetic_pairs/`, and "
        "`v6_synthetic_pairs/` were not modified.",
        "",
        "## Files",
        "",
        "| File | Schema | v4 | v5 | v6 | Total |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
        f"| `trajectories.jsonl` | `MinedTrajectory` | {traj.get('v4', 0)} | "
        f"{traj.get('v5', 0)} | {traj.get('v6', 0)} | {total_traj} |",
        f"| `safety_trajectories_synthetic.jsonl` | legacy safety trajectory | "
        f"{legacy.get('v4', 0)} | {legacy.get('v5', 0)} | {legacy.get('v6', 0)} | "
        f"{total_legacy} |",
        f"| `pairs.jsonl` | `ContrastPair` | {pairs.get('v4', 0)} | "
        f"{pairs.get('v5', 0)} | {pairs.get('v6', 0)} | {total_pairs} |",
        "",
        'Records are written **v4 then v5 then v6**. Every line has `"corpus": "v4"`, '
        '`"v5"`, or `"v6"` in addition to the original fields.',
        "",
        "## Regenerate",
        "",
        "```bash",
        "cd benchmarks/safety_monitor && uv run python -m safety_monitor v6-synth --merge",
        "```",
        "",
    ]
    return "\n".join(lines)


def merge_synthetic_pairs(out_dir: Path = V1) -> dict:
    """Write v4+v5+v6 merge into analysis_outputs/synthetic_pairs/."""
    out_dir.mkdir(parents=True, exist_ok=True)
    traj = _merge_jsonl(
        [(k, v["trajectories"]) for k, v in SOURCES.items()],
        out_dir / "trajectories.jsonl",
    )
    pair = _merge_jsonl(
        [(k, v["pairs"]) for k, v in SOURCES.items()],
        out_dir / "pairs.jsonl",
    )
    legacy = _merge_jsonl(
        [(k, v["legacy"]) for k, v in SOURCES.items()],
        out_dir / "safety_trajectories_synthetic.jsonl",
    )
    counts = {"trajectories": traj, "pairs": pair, "legacy": legacy}
    manifest = {
        "version": "v2",
        "outputs": {
            "trajectories": str(out_dir / "trajectories.jsonl"),
            "safety_trajectories": str(out_dir / "safety_trajectories_synthetic.jsonl"),
            "pairs": str(out_dir / "pairs.jsonl"),
            "readme": str(out_dir / "README.md"),
            "manifest": str(out_dir / "manifest.json"),
        },
        "counts": {
            "trajectories": {
                "schema": "MinedTrajectory",
                **traj,
                "total": sum(traj.values()),
            },
            "safety_trajectories": {
                "schema": "legacy_safety_trajectory",
                **legacy,
                "total": sum(legacy.values()),
            },
            "pairs": {
                "schema": "ContrastPair",
                **pair,
                "total": sum(pair.values()),
            },
        },
        "sources": {
            k: {sk: str(sv) for sk, sv in v.items()} for k, v in SOURCES.items()
        },
        "notes": {
            "order": "v4 then v5 then v6",
            "corpus_field": "each record has corpus in {v4, v5, v6}",
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (out_dir / "README.md").write_text(_readme(counts), encoding="utf-8")
    return manifest


def merge_v2(out_dir: Path = V2) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    traj_n = _merge_jsonl(
        [(k, v["trajectories"]) for k, v in SOURCES.items()],
        out_dir / "trajectories.jsonl",
    )
    pair_n = _merge_jsonl(
        [(k, v["pairs"]) for k, v in SOURCES.items()],
        out_dir / "pairs.jsonl",
    )
    legacy_n = _merge_jsonl(
        [(k, v["legacy"]) for k, v in SOURCES.items()],
        out_dir / "safety_trajectories_synthetic.jsonl",
    )
    counts = {
        "trajectories": sum(traj_n.values()),
        "pairs": sum(pair_n.values()),
        "legacy": sum(legacy_n.values()),
    }
    manifest = {
        "version": "v2",
        "outputs": {
            "trajectories": str(out_dir / "trajectories.jsonl"),
            "pairs": str(out_dir / "pairs.jsonl"),
            "legacy": str(out_dir / "safety_trajectories_synthetic.jsonl"),
            "manifest": str(out_dir / "manifest.json"),
        },
        "counts": counts,
        "sources": {
            k: {sk: str(sv) for sk, sv in v.items()} for k, v in SOURCES.items()
        },
        "notes": {"v1_preserved": str(V1), "order": "v4 then v5 then v6"},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def merge_all() -> dict[str, dict]:
    return {
        "synthetic_pairs": merge_synthetic_pairs(),
        "synthetic_pairs_v2": merge_v2(),
    }
