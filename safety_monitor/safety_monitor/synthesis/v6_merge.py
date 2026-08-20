"""Merge v4 + v5 + v6 synthetic corpora into synthetic_pairs_v2 without mutating v1."""

from __future__ import annotations

import json
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[4]
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


def _merge_jsonl(paths: list[tuple[str, Path]], out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as sink:
        for corpus, path in paths:
            if not path.exists():
                continue
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    record.setdefault("corpus", corpus)
                    sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                    n += 1
    return n


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
    counts = {"trajectories": traj_n, "pairs": pair_n, "legacy": legacy_n}
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
