"""Shallow-probe classifier gate for synthetic corpus label leakage.

A bag-of-ngrams logistic classifier on post-divergence trajectory text measures
how easily harmful vs clean halves can be told apart. This is a *measurement*
gate, not a generator fix: if AUROC is too high on a frozen corpus, we stop and
report rather than mutating JSONL outputs.

**Ceiling rationale**

- **Harmful semantics (~0.67 AUROC expected).** Safe halves refuse, verify, and
  document; harmful halves commit. That vocabulary skew is real label signal but
  not trivial leakage — a shallow probe should land well below 0.85.
- **Opaque markers (~1.0 AUROC).** Legacy ``sm_``/``hm_`` instance-id prefixes
  (or any half-specific token planted in every safe trajectory) would be
  perfectly separable; the gate fails above 0.85 to catch that class of leak.
- **Threshold 0.85.** Leaves headroom above the ~0.67 semantic ceiling while
  rejecting near-perfect separators indicative of accidental label encoding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline

from safety_monitor.synthesis.pairs import CLEAN, HARMFUL, LabeledStep, MinedTrajectory


PROBE_AUROC_MAX = 0.85
N_SPLITS = 5
TOP_FEATURES = 25

WORD_VECTORIZER: dict[str, Any] = {
    "ngram_range": (1, 2),
    "token_pattern": r"[a-zA-Z0-9_]+",
}
CHAR_VECTORIZER: dict[str, Any] = {
    "analyzer": "char_wb",
    "ngram_range": (3, 5),
    "max_features": 50_000,
}


@dataclass(frozen=True)
class ProbeResult:
    name: str
    mean_auroc: float
    std_auroc: float
    top_features: list[dict[str, float]]


def _args_json(step: LabeledStep) -> str:
    return json.dumps(step.arguments, sort_keys=True)


def divergence_index(
    harm_steps: list[LabeledStep], clean_steps: list[LabeledStep]
) -> int:
    """First step index where ``json.dumps(arguments)`` differs between halves."""
    n = min(len(harm_steps), len(clean_steps))
    for i in range(n):
        if _args_json(harm_steps[i]) != _args_json(clean_steps[i]):
            return i
    return n


def build_post_divergence_document(steps: list[LabeledStep], start: int) -> str:
    """Concatenate summary, observation, and serialised arguments from *start*."""
    parts: list[str] = []
    for step in steps[start:]:
        if step.summary:
            parts.append(step.summary)
        if step.observation:
            parts.append(step.observation)
        parts.append(_args_json(step))
    return "\n".join(parts)


def pair_trajectories(
    trajectories: list[MinedTrajectory],
) -> list[tuple[str, MinedTrajectory, MinedTrajectory]]:
    """Group trajectories by ``instance_id`` into (id, harmful, clean) tuples."""
    halves: dict[str, dict[str, MinedTrajectory]] = {}
    for traj in trajectories:
        halves.setdefault(traj.instance_id, {})[traj.role] = traj
    pairs: list[tuple[str, MinedTrajectory, MinedTrajectory]] = []
    for instance_id in sorted(halves):
        by_role = halves[instance_id]
        harmful = by_role.get(HARMFUL)
        clean = by_role.get(CLEAN)
        if harmful is None or clean is None:
            raise ValueError(
                f"{instance_id}: expected harmful and clean halves, got {sorted(by_role)}"
            )
        pairs.append((instance_id, harmful, clean))
    return pairs


def _prepare_corpus(
    pairs: list[tuple[str, MinedTrajectory, MinedTrajectory]],
) -> tuple[list[str], list[int], list[str]]:
    docs: list[str] = []
    labels: list[int] = []
    groups: list[str] = []
    for instance_id, harmful, clean in pairs:
        div = divergence_index(harmful.steps, clean.steps)
        docs.append(build_post_divergence_document(harmful.steps, div))
        labels.append(1)
        groups.append(instance_id)
        docs.append(build_post_divergence_document(clean.steps, div))
        labels.append(0)
        groups.append(instance_id)
    return docs, labels, groups


def _make_pipeline(vectorizer_kwargs: dict[str, Any]) -> Pipeline:
    return Pipeline(
        [
            ("tfidf", TfidfVectorizer(**vectorizer_kwargs)),
            ("clf", LogisticRegression(max_iter=2000)),
        ]
    )


def _cross_val_auroc(
    docs: list[str],
    labels: list[int],
    groups: list[str],
    vectorizer_kwargs: dict[str, Any],
) -> tuple[float, float]:
    y = np.asarray(labels)
    group_arr = np.asarray(groups)
    gkf = GroupKFold(n_splits=N_SPLITS)
    scores: list[float] = []
    for train_idx, test_idx in gkf.split(docs, y, group_arr):
        pipe = _make_pipeline(vectorizer_kwargs)
        train_docs = [docs[i] for i in train_idx]
        pipe.fit(train_docs, y[train_idx])
        proba = pipe.predict_proba([docs[i] for i in test_idx])[:, 1]
        scores.append(float(roc_auc_score(y[test_idx], proba)))
    return float(np.mean(scores)), float(np.std(scores))


def _top_word_features(docs: list[str], labels: list[int]) -> list[dict[str, Any]]:
    pipe = _make_pipeline(WORD_VECTORIZER)
    pipe.fit(docs, labels)
    coef = pipe.named_steps["clf"].coef_[0]
    names = pipe.named_steps["tfidf"].get_feature_names_out()
    ranked = sorted(
        ((str(name), float(weight)) for name, weight in zip(names, coef, strict=True)),
        key=lambda item: -abs(item[1]),
    )
    return [
        {"feature": name, "coefficient": weight}
        for name, weight in ranked[:TOP_FEATURES]
    ]


def run_probe_gate(
    trajectories: list[MinedTrajectory],
    *,
    max_auroc: float = PROBE_AUROC_MAX,
) -> dict[str, Any]:
    """Run word and char shallow probes; return a report dict and pass/fail status."""
    pairs = pair_trajectories(trajectories)
    docs, labels, groups = _prepare_corpus(pairs)

    word_mean, word_std = _cross_val_auroc(docs, labels, groups, WORD_VECTORIZER)
    char_mean, char_std = _cross_val_auroc(docs, labels, groups, CHAR_VECTORIZER)
    top_features = _top_word_features(docs, labels)

    ok = word_mean <= max_auroc and char_mean <= max_auroc
    reason = (
        f"word AUROC {word_mean:.3f}±{word_std:.3f}, "
        f"char AUROC {char_mean:.3f}±{char_std:.3f}"
    )
    if not ok:
        reason = f"probe AUROC above {max_auroc}: {reason}"

    return {
        "n_pairs": len(pairs),
        "max_auroc": max_auroc,
        "ok": ok,
        "reason": reason,
        "word_probe": {
            "mean_auroc": round(word_mean, 4),
            "std_auroc": round(word_std, 4),
            "top_features": top_features,
        },
        "char_probe": {
            "mean_auroc": round(char_mean, 4),
            "std_auroc": round(char_std, 4),
        },
    }


def group_kfold_assignments(
    groups: list[str], *, n_splits: int = N_SPLITS
) -> dict[str, int]:
    """Map each group id to its GroupKFold fold index (for integrity checks)."""
    unique_groups = sorted(set(groups))
    group_to_rows: dict[str, list[int]] = {g: [] for g in unique_groups}
    for idx, group in enumerate(groups):
        group_to_rows[group].append(idx)
    y = np.zeros(len(groups))
    group_arr = np.asarray(groups)
    assignments: dict[str, int] = {}
    gkf = GroupKFold(n_splits=n_splits)
    for fold_idx, (_, test_idx) in enumerate(
        gkf.split(np.arange(len(groups)), y, group_arr)
    ):
        test_groups = {groups[i] for i in test_idx}
        for group in test_groups:
            assignments[group] = fold_idx
    return assignments
