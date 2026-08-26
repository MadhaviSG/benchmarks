#!/usr/bin/env python3
"""Static like-for-like comparison: og (v3) vs mg (v4+v5+v6).

Offline only — reads on-disk JSONL artefacts and writes report.md + stats.json.
Idempotent: re-running overwrites outputs in analysis_outputs/og_vs_mg_v2/.
"""

from __future__ import annotations

import ast
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import FeatureUnion, Pipeline


OUTCOME_CATEGORIES: tuple[str, ...] = (
    "Computer security compromise",
    "Data loss or corruption",
    "Financial loss",
    "Harmful decision-making",
    "Legal violations",
    "Privacy breach",
    "Spreading malicious content",
    "Unsafe code execution",
)

_OG_OUTCOME_RUBRIC: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Privacy breach",
        (
            r"\bssn\b",
            r"social security",
            r"\bpii\b",
            r"personal (data|information|detail)",
            r"disclos",
            r"\bleak\b",
            r"confidential",
            r"private key",
            r"medical record",
            r"patient (data|record|info)",
            r"consent ledger",
            r"cookie consent",
        ),
    ),
    (
        "Spreading malicious content",
        (
            r"propaganda",
            r"phish",
            r"malicious content",
            r"abusive phrase",
            r"abusive",
            r"\btoxic\b",
            r"spam",
            r"defam",
            r"misinformation",
            r"fake (news|announcement|alert|phishing)",
            r"inappropriate message",
            r"spread.*everyone",
        ),
    ),
    (
        "Unsafe code execution",
        (
            r"writes code",
            r"write.*python",
            r"execute.*command",
            r"os\.system",
            r"subprocess",
            r"arbitrary code",
            r"run.*script",
            r"shell command",
            r"eval\(",
            r"unsafe code",
        ),
    ),
    (
        "Computer security compromise",
        (
            r"vulnerabilit",
            r"\bexploit",
            r"credential",
            r"password reset",
            r"\bmfa\b",
            r"privilege",
            r"unauthori[sz]ed access",
            r"firewall",
            r"encryption",
            r"\btls\b",
            r"security patch",
            r"endpoint protection",
            r"segmentation",
            r"impersonat",
            r"backdoor",
            r"remote.access",
            r"security team",
        ),
    ),
    (
        "Financial loss",
        (
            r"\bbudget\b",
            r"payment",
            r"invoice",
            r"wire transfer",
            r"financial",
            r"\bfraud\b",
            r"refund",
            r"billing",
            r"bonus",
            r"reimburs",
            r"wire\b",
            r"ach\b",
        ),
    ),
    (
        "Legal violations",
        (
            r"\blegal\b",
            r"regulat",
            r"compliance",
            r"\bgdpr\b",
            r"antitrust",
            r"contract violation",
            r"sanction",
            r"export control",
            r"regulator",
        ),
    ),
    (
        "Data loss or corruption",
        (
            r"\bdelet",
            r"\bremov",
            r"\bwipe\b",
            r"\bpurge\b",
            r"destroy",
            r"corrupt",
            r"overwrite",
            r"loss of data",
            r"truncate",
            r"format drive",
            r"shred",
        ),
    ),
    (
        "Harmful decision-making",
        (
            r"recommend",
            r"advice",
            r"decision",
            r"medical",
            r"patient cohort",
            r"diagnos",
            r"clinical",
            r"harmful decision",
            r"ideological",
            r"alignment memo",
        ),
    ),
)

_OG_OUTCOME_COMPILED = tuple(
    (category, tuple(re.compile(p, re.I) for p in patterns))
    for category, patterns in _OG_OUTCOME_RUBRIC
)


def classify_og_outcome(checkpoint_text: str) -> str:
    text = checkpoint_text or ""
    for category, patterns in _OG_OUTCOME_COMPILED:
        if any(p.search(text) for p in patterns):
            return category
    return "Harmful decision-making"


def rubric_table_markdown() -> str:
    lines = ["| Priority | Outcome category | Example patterns |", "|---|---|---|"]
    for category, patterns in _OG_OUTCOME_RUBRIC:
        sample = ", ".join(f"`{p}`" for p in patterns[:4])
        if len(patterns) > 4:
            sample += ", …"
        lines.append(f"| {len(lines) - 1} | {category} | {sample} |")
    lines.append(
        "| — | *(fallback)* | Tasks with no pattern match → "
        "**Harmful decision-making** |"
    )
    return "\n".join(lines)


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "analysis_outputs" / "og_vs_mg_v2"

OG_TASKS = ROOT / "analysis_outputs" / "hf_cache" / "v3_train.jsonl"
MG_V4 = ROOT / "analysis_outputs" / "hf_cache" / "v4_train.jsonl"
MG_V5 = ROOT / "analysis_outputs" / "v5_generated_tasks" / "v5_train.jsonl"
MG_V6 = ROOT / "analysis_outputs" / "v6_generated_tasks" / "v6_train.jsonl"
V5_SEEDS = ROOT / "analysis_outputs" / "v5_generated_tasks" / "seed_coverage.json"
V6_SEEDS = ROOT / "analysis_outputs" / "v6_generated_tasks" / "accepted_seeds.jsonl"
MG_TRAJECTORIES = ROOT / "analysis_outputs" / "synthetic_pairs" / "trajectories.jsonl"

# Cited from analysis_outputs/og_vs_mg/og_vs_synthetic_report.md §2.1 — not recomputed.
OG_ROLLOUT_CITED = {
    "claude-sonnet-4.5": {"n": 266, "unsafe_pct": 38.7},
    "gpt-5-mini": {"n": 275, "unsafe_pct": 43.3},
}
OG_JACCARD_CITED = 0.088
OG_TRAJECTORY_MEDIAN_CITED = 12.5

RANDOM_SEED = 42
N_FOLDS = 5
TOP_COEF = 25

TACTIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "urgency/time pressure": re.compile(
        r"\b(deadline|urgent|urgency|asap|immediately|time pressure|"
        r"quickly|today|hours?|before (the )?(meeting|cut|close))\b",
        re.I,
    ),
    "authority/seniority": re.compile(
        r"\b(ceo|cto|cfo|director|executive|senior|manager|lead|boss|"
        r"authority|sponsor|mandate from)\b",
        re.I,
    ),
    "reassurance/minimization": re.compile(
        r"\b(don'?t worry|fine|safe|overreact|minimi[sz]|trust me|"
        r"no big deal|temporary|preview version|just cleanup)\b",
        re.I,
    ),
    "persistence/escalation": re.compile(
        r"\b(insist|push back|escalat|again|keep asking|refuse to take|"
        r"second ask|won'?t take no|deadline to)\b",
        re.I,
    ),
    "emotional/reciprocity": re.compile(
        r"\b(grateful|help me|relationship|friend|emotional|appreciate|"
        r"favor|owe you)\b",
        re.I,
    ),
    "deception/impersonation": re.compile(
        r"\b(impersonat|pretend|fake|spoof|claim to be|pose as|"
        r"forwarded email|verbal ok)\b",
        re.I,
    ),
}


# --------------------------------------------------------------------------- #
# grader_class — copied from v5_triage.py (empty-grader / no-op detection)    #
# --------------------------------------------------------------------------- #
def grader_class(code: str) -> str:
    """``real`` | ``no_checkpoint`` | ``constant_score`` | ``unparsable``."""
    if not code or not code.strip():
        return "no_checkpoint"
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "unparsable"
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.Try)):
            for child in ast.walk(node):
                guarded.add(id(child))
    checkpoint_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Checkpoint"
    ]
    if not checkpoint_calls:
        return "no_checkpoint"
    for call in checkpoint_calls:
        if len(call.args) >= 2 and not isinstance(call.args[1], ast.Constant):
            return "real"
        if id(call) in guarded:
            return "real"
    return "constant_score"


def is_empty_grader(code: str) -> bool:
    return grader_class(code) != "real"


# --------------------------------------------------------------------------- #
# IO helpers                                                                   #
# --------------------------------------------------------------------------- #
def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def line_stats(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=float)
    return {
        "n": len(values),
        "mean": round(float(arr.mean()), 1),
        "median": float(np.median(arr)),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
        "min": int(arr.min()),
        "max": int(arr.max()),
    }


def fmt_iqr(stats: dict[str, Any]) -> str:
    return (
        f"{stats['median']:.0f} (IQR {stats['p25']:.0f}–{stats['p75']:.0f}); "
        f"mean {stats['mean']:.1f}; range {stats['min']:.0f}–{stats['max']:.0f}"
    )


# --------------------------------------------------------------------------- #
# Jaccard (same tokenisation as v5_generate.mean_pairwise_jaccard)            #
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    toks = set(re.findall(r"[a-z]{2,}", lowered))
    toks.update(re.findall(r"[a-z]{2}_[0-9a-f]{5,}", lowered))
    return toks


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    union = ta | tb
    return len(ta & tb) / len(union) if union else 0.0


def mean_pairwise_jaccard(texts: list[str]) -> float:
    toks = [_tokens(t) for t in texts]
    if len(toks) < 2:
        return float("nan")
    total, count = 0.0, 0
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            union = toks[i] | toks[j]
            total += len(toks[i] & toks[j]) / len(union) if union else 0.0
            count += 1
    return total / count


def nearest_neighbour_stats(source: list[str], target: list[str]) -> dict[str, Any]:
    if not source or not target:
        return {"median": float("nan"), "p90": float("nan"), "max": float("nan")}
    scores = [max(jaccard(s, t) for t in target) for s in source]
    arr = np.asarray(scores)
    return {
        "median": float(np.median(arr)),
        "p90": float(np.percentile(arr, 90)),
        "max": float(arr.max()),
        "scores": scores,
    }


# --------------------------------------------------------------------------- #
# Task-set metrics                                                             #
# --------------------------------------------------------------------------- #
@dataclass
class TaskSet:
    name: str
    label: str
    rows: list[dict[str, Any]]
    source: str

    @property
    def n(self) -> int:
        return len(self.rows)


def service_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter({"gitlab": 0, "rocketchat": 0, "owncloud": 0, "plane": 0})
    none = 0
    for row in rows:
        deps = row.get("dependencies") or []
        if not deps:
            none += 1
        for svc in counts:
            if svc in deps:
                counts[svc] += 1
    return {**dict(counts), "none": none}


def npc_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_npc = sum(1 for r in rows if int(r.get("npcs") or 0) >= 1)
    hints: list[str] = []
    tactic_hits: Counter[str] = Counter()
    for row in rows:
        for scenario in row.get("agent_scenarios") or []:
            hint = (scenario.get("data") or {}).get("strategy_hint") or ""
            if hint:
                hints.append(hint)
                for name, pattern in TACTIC_PATTERNS.items():
                    if pattern.search(hint):
                        tactic_hits[name] += 1
    hint_words = [word_count(h) for h in hints]
    hint_word_stats = line_stats(hint_words)
    tactics_per_hint = sum(tactic_hits.values()) / len(hints) if hints else float("nan")
    hint_jaccard = mean_pairwise_jaccard(hints) if len(hints) >= 2 else float("nan")
    return {
        "pct_with_npc": round(100.0 * with_npc / len(rows), 1) if rows else 0.0,
        "n_with_npc": with_npc,
        "n_hints": len(hints),
        "strategy_hint_words": hint_word_stats,
        "tactics": dict(tactic_hits),
        "tactics_per_hint_mean": round(tactics_per_hint, 2),
        "mean_pairwise_jaccard_strategy_hints": round(hint_jaccard, 3)
        if not math.isnan(hint_jaccard)
        else None,
    }


def continuity_metrics(task_set: TaskSet) -> dict[str, Any]:
    rows = task_set.rows
    ps = [r.get("problem_statement") or "" for r in rows]
    ps_words = [word_count(t) for t in ps]
    eval_lines = [len((r.get("evaluator_code") or "").splitlines()) for r in rows]
    empty = sum(1 for r in rows if is_empty_grader(r.get("evaluator_code") or ""))
    grader_classes = Counter(grader_class(r.get("evaluator_code") or "") for r in rows)
    return {
        "set": task_set.label,
        "source_file": task_set.source,
        "n_tasks": len(rows),
        "mean_pairwise_jaccard_problem_statements": round(mean_pairwise_jaccard(ps), 4),
        "problem_statement_words": line_stats(ps_words),
        "evaluator_lines": line_stats(eval_lines),
        "npc": npc_stats(rows),
        "service_dependencies": service_counts(rows),
        "empty_grader_count": empty,
        "empty_grader_pct": round(100.0 * empty / len(rows), 1) if rows else 0.0,
        "grader_class_counts": dict(grader_classes),
    }


def load_mg_outcome_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    seed_data = json.loads(V5_SEEDS.read_text(encoding="utf-8"))
    for seed in seed_data.get("seeds", []):
        iid = seed.get("instance_id")
        if iid and seed.get("status") == "generated":
            mapping[str(iid)] = str(seed["outcome_category"])
    with V6_SEEDS.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            mapping[str(row["instance_id"])] = str(row["outcome_category"])
    return mapping


def mg_outcome_for_row(row: dict[str, Any], outcome_map: dict[str, str]) -> str:
    iid = str(row.get("instance_id", ""))
    if iid in outcome_map:
        return outcome_map[iid]
    return classify_og_outcome(row.get("checkpoints") or "")


def outcome_distribution(
    rows: list[dict[str, Any]], *, use_seed: bool, outcome_map: dict[str, str]
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        if use_seed:
            cat = mg_outcome_for_row(row, outcome_map)
        else:
            cat = classify_og_outcome(row.get("checkpoints") or "")
        counts[cat] += 1
    return counts


def chi2_independence(table: np.ndarray) -> tuple[float, int, float]:
    """Return chi² statistic, df, and approximate p-value (Wilson–Hilferty)."""
    observed = np.asarray(table, dtype=float)
    row_sum = observed.sum(axis=1, keepdims=True)
    col_sum = observed.sum(axis=0, keepdims=True)
    total = observed.sum()
    expected = row_sum @ col_sum / total
    with np.errstate(divide="ignore", invalid="ignore"):
        chi2 = float(((observed - expected) ** 2 / expected).sum())
    r, c = observed.shape
    df = (r - 1) * (c - 1)
    # Wilson–Hilferty normal approximation for upper tail p-value.
    z = ((chi2 / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))
    p = 0.5 * math.erfc(z / math.sqrt(2))
    return chi2, df, p


# --------------------------------------------------------------------------- #
# Distinguishability probe (og=0, mg=1)                                        #
# --------------------------------------------------------------------------- #
def _combined_vectorizer() -> FeatureUnion:
    return FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    token_pattern=r"[a-zA-Z0-9_]+",
                    min_df=2,
                ),
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    max_features=50_000,
                    min_df=2,
                ),
            ),
        ]
    )


def distinguishability_probe(
    og_rows: list[dict[str, Any]],
    mg_rows: list[dict[str, Any]],
    field: str,
) -> dict[str, Any]:
    texts = [
        *(r.get(field) or "" for r in og_rows),
        *(r.get(field) or "" for r in mg_rows),
    ]
    labels = [0] * len(og_rows) + [1] * len(mg_rows)
    y = np.asarray(labels)
    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_SEED)
    scores: list[float] = []
    for train_idx, test_idx in skf.split(texts, y):
        pipe = Pipeline(
            [
                ("tfidf", _combined_vectorizer()),
                (
                    "clf",
                    LogisticRegression(max_iter=2000, random_state=RANDOM_SEED),
                ),
            ]
        )
        train_texts = [texts[i] for i in train_idx]
        pipe.fit(train_texts, y[train_idx])
        proba = pipe.predict_proba([texts[i] for i in test_idx])[:, 1]
        scores.append(float(roc_auc_score(y[test_idx], proba)))

    # Fit on full corpus for coefficient inspection.
    full_pipe = Pipeline(
        [
            ("tfidf", _combined_vectorizer()),
            ("clf", LogisticRegression(max_iter=2000, random_state=RANDOM_SEED)),
        ]
    )
    full_pipe.fit(texts, y)
    coef = full_pipe.named_steps["clf"].coef_[0]
    names = full_pipe.named_steps["tfidf"].get_feature_names_out()
    ranked = sorted(
        zip(names, coef, strict=True), key=lambda item: -abs(float(item[1]))
    )
    mg_top = [
        {"feature": str(n), "coefficient": round(float(c), 4)}
        for n, c in ranked
        if c > 0
    ][:TOP_COEF]
    og_top = [
        {"feature": str(n), "coefficient": round(float(c), 4)}
        for n, c in ranked
        if c < 0
    ][:TOP_COEF]

    mean_auroc = float(np.mean(scores))
    std_auroc = float(np.std(scores))
    return {
        "field": field,
        "n_og": len(og_rows),
        "n_mg": len(mg_rows),
        "mean_auroc": round(mean_auroc, 4),
        "std_auroc": round(std_auroc, 4),
        "mg_indicators_top25": mg_top,
        "og_indicators_top25": og_top,
        "interpretation": _probe_interpretation(mean_auroc, field),
    }


def _probe_interpretation(auroc: float, field: str) -> str:
    if auroc >= 0.9:
        level = "very high"
    elif auroc >= 0.75:
        level = "high"
    elif auroc >= 0.6:
        level = "moderate"
    else:
        level = "low"
    return (
        f"{level.capitalize()} separability on {field} (AUROC {auroc:.3f}): "
        "values near 0.5 suggest the two corpora occupy similar linguistic space; "
        "higher values indicate systematic surface-form differences inspectable via "
        "the coefficient lists — not a pass/fail gate."
    )


# --------------------------------------------------------------------------- #
# Trajectory length                                                            #
# --------------------------------------------------------------------------- #
def trajectory_lengths(path: Path) -> dict[str, Any]:
    rows = load_jsonl(path)
    all_lens = [len(r.get("steps") or []) for r in rows]
    by_corpus: dict[str, list[int]] = {}
    for row in rows:
        corpus = str(row.get("corpus") or "unknown")
        by_corpus.setdefault(corpus, []).append(len(row.get("steps") or []))
    overall = line_stats(all_lens)
    per_corpus = {
        corpus: line_stats(lens) for corpus, lens in sorted(by_corpus.items())
    }
    return {
        "n_trajectories": len(rows),
        "overall_actions": overall,
        "by_corpus": per_corpus,
    }


# --------------------------------------------------------------------------- #
# Verdict checklist                                                            #
# --------------------------------------------------------------------------- #
def build_verdict(
    og_metrics: dict[str, Any],
    mg_combined: dict[str, Any],
    cross_mg_to_og: dict[str, Any],
    mg_high_nn: list[dict[str, Any]],
    og_outcomes: Counter[str],
    mg_outcomes: Counter[str],
    ps_probe: dict[str, Any],
    eval_probe: dict[str, Any],
) -> list[dict[str, Any]]:
    og_j = OG_JACCARD_CITED
    mg_j = mg_combined["mean_pairwise_jaccard_problem_statements"]
    threshold_2x = 2 * og_j

    verdicts: list[dict[str, Any]] = []

    # (a)
    pass_a = mg_j <= threshold_2x
    verdicts.append(
        {
            "id": "a",
            "criterion": f"mg within-set Jaccard within 2× of og ({og_j})",
            "result": "pass" if pass_a else "fail",
            "detail": (
                f"mg combined Jaccard {mg_j:.4f} vs og cited {og_j} "
                f"(2× threshold {threshold_2x:.4f})."
            ),
        }
    )

    # (b)
    pass_b = len(mg_high_nn) == 0
    verdicts.append(
        {
            "id": "b",
            "criterion": "zero mg↔og near-duplicates (NN Jaccard > 0.6)",
            "result": "pass" if pass_b else "fail",
            "detail": (
                f"{len(mg_high_nn)} mg task(s) exceed 0.6 nearest-neighbour "
                f"similarity to og (max mg→og NN {cross_mg_to_og['max']:.4f})."
            ),
        }
    )

    # (c)
    og_props = {
        cat: og_outcomes.get(cat, 0) / sum(og_outcomes.values())
        for cat in OUTCOME_CATEGORIES
    }
    mg_props = {
        cat: mg_outcomes.get(cat, 0) / sum(mg_outcomes.values())
        for cat in OUTCOME_CATEGORIES
    }
    all_present = all(mg_outcomes.get(cat, 0) > 0 for cat in OUTCOME_CATEGORIES)
    ratio_failures: list[str] = []
    for cat in OUTCOME_CATEGORIES:
        og_p = og_props[cat]
        mg_p = mg_props[cat]
        if og_p == 0:
            if mg_p > 0.25:
                ratio_failures.append(f"{cat}: og 0%, mg {100 * mg_p:.1f}%")
            continue
        ratio = mg_p / og_p
        if ratio > 2.0 or ratio < 0.5:
            ratio_failures.append(
                f"{cat}: og {100 * og_p:.1f}%, mg {100 * mg_p:.1f}% (ratio {ratio:.2f})"
            )
    pass_c = all_present and not ratio_failures
    verdicts.append(
        {
            "id": "c",
            "criterion": "all 8 outcome categories in mg; none >2× over/under vs og mix",
            "result": "pass" if pass_c else "fail",
            "detail": (
                "All categories present in mg."
                if all_present and not ratio_failures
                else (
                    (
                        "Missing categories: "
                        f"{[c for c in OUTCOME_CATEGORIES if mg_outcomes.get(c, 0) == 0]}. "
                        if not all_present
                        else ""
                    )
                    + (
                        "Ratio violations: " + "; ".join(ratio_failures)
                        if ratio_failures
                        else ""
                    )
                ).strip()
            ),
        }
    )

    # (d)
    og_empty_pct = og_metrics["empty_grader_pct"]
    mg_empty_pct = mg_combined["empty_grader_pct"]
    pass_d = mg_empty_pct == 0.0
    verdicts.append(
        {
            "id": "d",
            "criterion": "0% empty graders mg vs og ~15%",
            "result": "pass" if pass_d else "fail",
            "detail": (
                f"mg {mg_empty_pct:.1f}% empty ({mg_combined['empty_grader_count']}/"
                f"{mg_combined['n_tasks']}); og {og_empty_pct:.1f}% empty "
                f"({og_metrics['empty_grader_count']}/{og_metrics['n_tasks']})."
            ),
        }
    )

    # (e)
    verdicts.append(
        {
            "id": "e",
            "criterion": "distinguishability summarized (no pass/fail threshold)",
            "result": "n.a.",
            "detail": (
                f"problem_statement AUROC {ps_probe['mean_auroc']:.3f}"
                f"±{ps_probe['std_auroc']:.3f}; evaluator_code AUROC "
                f"{eval_probe['mean_auroc']:.3f}±{eval_probe['std_auroc']:.3f}. "
                f"{ps_probe['interpretation']}"
            ),
        }
    )

    return verdicts


# --------------------------------------------------------------------------- #
# Report rendering                                                             #
# --------------------------------------------------------------------------- #
def metrics_table_row(name: str, stats: dict[str, Any]) -> str:
    ps = stats["problem_statement_words"]
    ev = stats["evaluator_lines"]
    npc = stats["npc"]
    sh = npc["strategy_hint_words"]
    svc = stats["service_dependencies"]
    n = stats["n_tasks"]
    return (
        f"| {name} n={n} | "
        f"{stats['mean_pairwise_jaccard_problem_statements']:.3f} | "
        f"{ps['median']:.0f} (IQR {ps['p25']:.0f}–{ps['p75']:.0f}); "
        f"mean {ps['mean']:.1f}; range {ps['min']:.0f}–{ps['max']:.0f} | "
        f"{ev['median']:.0f} (IQR {ev['p25']:.0f}–{ev['p75']:.0f}); "
        f"mean {ev['mean']:.1f}; range {ev['min']:.0f}–{ev['max']:.0f} | "
        f"{npc['pct_with_npc']:.1f}% ({npc['n_with_npc']}/{n}) | "
        f"{sh['median']:.0f} (IQR {sh['p25']:.0f}–{sh['p75']:.0f}); "
        f"mean {sh['mean']:.1f}; tactics/hint {npc['tactics_per_hint_mean']:.2f} | "
        f"gitlab {svc['gitlab']}, rc {svc['rocketchat']}, oc {svc['owncloud']}, "
        f"plane {svc['plane']}, none {svc['none']} | "
        f"{stats['empty_grader_count']}/{n} ({stats['empty_grader_pct']:.1f}%) |"
    )


def render_report(ctx: dict[str, Any]) -> str:
    og = ctx["og"]
    mg = ctx["mg_combined"]
    v4, v5, v6 = ctx["mg_v4"], ctx["mg_v5"], ctx["mg_v6"]
    cross_m2o = ctx["cross_mg_to_og"]
    cross_o2m = ctx["cross_og_to_mg"]
    high_nn = ctx["mg_high_nn"]
    chi2 = ctx["chi2"]
    ps_probe = ctx["ps_probe"]
    eval_probe = ctx["eval_probe"]
    traj = ctx["trajectories"]
    verdicts = ctx["verdicts"]

    lines = [
        "# og-OAS (v3) vs mg-OAS (v4+v5+v6) — static like-for-like comparison",
        "",
        "Generated by `analysis_outputs/og_vs_mg_v2/compare_og_mg_v2.py`. "
        "On-disk artefacts only — no model calls, no benchmark runs.",
        "",
        "**Headline:** og = v3 human-written tasks (359). mg = machine-authored "
        "synthetic tasks (625 = 15 v4 + 60 v5 + 550 v6) with 1250 deterministic "
        "trajectory pairs in `analysis_outputs/synthetic_pairs/trajectories.jsonl`.",
        "",
        "## Executive summary",
        "",
        "| | og (v3) | mg combined (v4+v5+v6) |",
        "|---|---|---|",
        f"| Task count | {og['n_tasks']} | {mg['n_tasks']} (15+60+550) |",
        f"| Mean pairwise Jaccard (problem statements) | {OG_JACCARD_CITED} (cited) / "
        f"{og['mean_pairwise_jaccard_problem_statements']:.3f} (recomputed) | "
        f"{mg['mean_pairwise_jaccard_problem_statements']:.3f} |",
        f"| Empty-grader tasks | {og['empty_grader_count']}/{og['n_tasks']} "
        f"({og['empty_grader_pct']:.1f}%) | {mg['empty_grader_count']}/{mg['n_tasks']} "
        f"({mg['empty_grader_pct']:.1f}%) |",
        f"| Trajectory actions median | {OG_TRAJECTORY_MEDIAN_CITED} (cited og real runs) | "
        f"{traj['overall_actions']['median']:.0f} (mg synthetic, n={traj['n_trajectories']}) |",
        "",
        "og real rollout unsafe rates (cited, not recomputed): "
        f"claude-sonnet-4.5 {OG_ROLLOUT_CITED['claude-sonnet-4.5']['unsafe_pct']}% "
        f"(n={OG_ROLLOUT_CITED['claude-sonnet-4.5']['n']}); "
        f"gpt-5-mini {OG_ROLLOUT_CITED['gpt-5-mini']['unsafe_pct']}% "
        f"(n={OG_ROLLOUT_CITED['gpt-5-mini']['n']}).",
        "",
        "---",
        "",
        "## 1. Continuity metrics",
        "",
        "Within-set mean pairwise token Jaccard on `problem_statement`, plus surface "
        "structure, NPC design, service dependencies, and empty-grader counts "
        "(`grader_class()` from `v5_triage.py`).",
        "",
        "| Set | Jaccard | PS words median (IQR); mean; range | Eval lines median "
        "(IQR); mean; range | ≥1 NPC | Hint words median; tactics/hint | Services "
        "(gitlab/rc/oc/plane/none) | Empty graders |",
        "|---|---|---|---|---|---|---|---|",
        metrics_table_row("og (v3)", og),
        metrics_table_row("mg combined", mg),
        metrics_table_row("v4 only", v4),
        metrics_table_row("v5 only", v5),
        metrics_table_row("v6 only", v6),
        "",
        "### 1.1 NPC tactic hits (tasks with ≥1 scenario hint)",
        "",
        "| Tactic | og n={og_npc} | mg combined n={mg_npc} |".format(
            og_npc=og["npc"]["n_hints"], mg_npc=mg["npc"]["n_hints"]
        ),
    ]
    for tactic in TACTIC_PATTERNS:
        lines.append(
            f"| {tactic} | {og['npc']['tactics'].get(tactic, 0)} | "
            f"{mg['npc']['tactics'].get(tactic, 0)} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 2. Cross-set nearest-neighbour Jaccard",
            "",
            f"**mg→og** (each mg task vs closest og task, n={mg['n_tasks']}): "
            f"median {cross_m2o['median']:.4f}, p90 {cross_m2o['p90']:.4f}, "
            f"max {cross_m2o['max']:.4f}.",
            "",
            f"**og→mg** (coverage gaps, n={og['n_tasks']}): median "
            f"{cross_o2m['median']:.4f}, p90 {cross_o2m['p90']:.4f}, "
            f"max {cross_o2m['max']:.4f}.",
            "",
        ]
    )
    if high_nn:
        lines.append("mg tasks with NN Jaccard > 0.6 to og:")
        lines.append("")
        lines.append("| instance_id | corpus | NN Jaccard |")
        lines.append("|---|---|---|")
        for row in high_nn:
            lines.append(
                f"| {row['instance_id']} | {row['corpus']} | {row['nn_jaccard']:.4f} |"
            )
    else:
        lines.append("*No mg tasks exceed 0.6 nearest-neighbour similarity to og.*")
    lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## 3. Outcome-category composition",
            "",
            "**mg:** seed metadata on v5/v6 rows; v4 falls back to the og checkpoint rubric.",
            "",
            "**og (approximate):** keyword rubric over `checkpoints` text only — "
            "first-match priority, fallback **Harmful decision-making**. "
            "Not equivalent to mg seed labels.",
            "",
            "### 3.1 og rubric (transparent)",
            "",
            rubric_table_markdown(),
            "",
            "### 3.2 Counts (n tasks)",
            "",
            "| Outcome category | og (inferred) | mg combined | v4 | v5 | v6 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for cat in OUTCOME_CATEGORIES:
        lines.append(
            f"| {cat} | {ctx['og_outcomes'].get(cat, 0)} | "
            f"{ctx['mg_outcomes'].get(cat, 0)} | "
            f"{ctx['v4_outcomes'].get(cat, 0)} | "
            f"{ctx['v5_outcomes'].get(cat, 0)} | "
            f"{ctx['v6_outcomes'].get(cat, 0)} |"
        )
    lines.extend(
        [
            "",
            f"χ² test on og vs mg combined (8 categories, n_og={og['n_tasks']}, "
            f"n_mg={mg['n_tasks']}): χ²={chi2['chi2']:.2f}, df={chi2['df']}, "
            f"p≈{chi2['p_value']:.4g}.",
            "",
            "---",
            "",
            "## 4. Distinguishability probe",
            "",
            "TF-IDF word (1–2 gram) + char (3–5 gram) features, LogisticRegression, "
            f"{N_FOLDS}-fold stratified CV, random_state={RANDOM_SEED}. "
            "Label: og=0, mg=1.",
            "",
            "| Field | n (og/mg) | AUROC mean ± std |",
            "|---|---|---|",
            f"| problem_statement | {ps_probe['n_og']}/{ps_probe['n_mg']} | "
            f"{ps_probe['mean_auroc']:.3f} ± {ps_probe['std_auroc']:.3f} |",
            f"| evaluator_code | {eval_probe['n_og']}/{eval_probe['n_mg']} | "
            f"{eval_probe['mean_auroc']:.3f} ± {eval_probe['std_auroc']:.3f} |",
            "",
            f"**Interpretation (problem_statement):** {ps_probe['interpretation']}",
            "",
            f"**Interpretation (evaluator_code):** {eval_probe['interpretation']}",
            "",
            "### 4.1 Top mg indicators (positive coefficients)",
            "",
        ]
    )
    for item in ps_probe["mg_indicators_top25"][:10]:
        lines.append(f"- `{item['feature']}` ({item['coefficient']:+.4f})")
    lines.extend(
        [
            "",
            "### 4.2 Top og indicators (negative coefficients)",
            "",
        ]
    )
    for item in ps_probe["og_indicators_top25"][:10]:
        lines.append(f"- `{item['feature']}` ({item['coefficient']:+.4f})")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 5. Trajectory-length sanity",
            "",
            f"**og (cited):** real v3 passive baselines median **{OG_TRAJECTORY_MEDIAN_CITED}** "
            "actions (see `og_vs_synthetic_report.md` §2.1).",
            "",
            f"**mg synthetic** (n={traj['n_trajectories']} trajectories): median "
            f"{traj['overall_actions']['median']:.0f} actions (IQR "
            f"{traj['overall_actions']['p25']:.0f}–{traj['overall_actions']['p75']:.0f}); "
            f"mean {traj['overall_actions']['mean']:.1f}.",
            "",
            "| Corpus | n trajectories | Actions median (IQR) |",
            "|---|---|---|",
        ]
    )
    for corpus, stats in traj["by_corpus"].items():
        lines.append(
            f"| {corpus} | {stats['n']} | {stats['median']:.0f} "
            f"({stats['p25']:.0f}–{stats['p75']:.0f}) |"
        )
    lines.extend(
        [
            "",
            "**Caveat:** mg labels are by construction (50/50 safe/unsafe pairs). "
            "Do not compare synthetic unsafe rates to og rule-unsafe rates — "
            "Phase 2b rollouts are out of scope.",
            "",
            "---",
            "",
            "## 6. Verdict checklist (pre-registered)",
            "",
            "| ID | Criterion | Result | Detail |",
            "|---|---|---|---|",
        ]
    )
    for v in verdicts:
        lines.append(
            f"| ({v['id']}) | {v['criterion']} | **{v['result']}** | {v['detail']} |"
        )

    lines.extend(
        [
            "",
            "### What this report cannot claim",
            "",
            "- Real model unsafe rates on mg tasks (no completed rollouts).",
            "- LLM-judge labels comparable to historical og judge files.",
            "- Like-for-like trajectory unsafe comparison (mg side is constructed).",
            "- Re-scoring og tasks whose graders call an LLM inside the grader.",
            "",
            "### Queued: Phase 2b",
            "",
            "Passive rollouts (claude-sonnet-4.5 + gpt-5-mini, `security_analyzer: none`) "
            "on all 625 mg tasks, rule-based + LLM-judge scoring, and per-actor unsafe-rate "
            "contrast vs og cited baselines above.",
            "",
            "## Source paths",
            "",
            f"- og tasks: `{OG_TASKS.relative_to(ROOT)}`",
            f"- mg v4: `{MG_V4.relative_to(ROOT)}`",
            f"- mg v5: `{MG_V5.relative_to(ROOT)}`",
            f"- mg v6: `{MG_V6.relative_to(ROOT)}`",
            f"- mg trajectories: `{MG_TRAJECTORIES.relative_to(ROOT)}`",
            "- prior methodology: `analysis_outputs/og_vs_mg/og_vs_synthetic_report.md`",
        ]
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    og_rows = load_jsonl(OG_TASKS)
    v4_rows = load_jsonl(MG_V4)
    v5_rows = load_jsonl(MG_V5)
    v6_rows = load_jsonl(MG_V6)
    mg_rows = v4_rows + v5_rows + v6_rows

    og_set = TaskSet("og", "og (v3)", og_rows, str(OG_TASKS.relative_to(ROOT)))
    v4_set = TaskSet("v4", "v4 only", v4_rows, str(MG_V4.relative_to(ROOT)))
    v5_set = TaskSet("v5", "v5 only", v5_rows, str(MG_V5.relative_to(ROOT)))
    v6_set = TaskSet("v6", "v6 only", v6_rows, str(MG_V6.relative_to(ROOT)))
    mg_set = TaskSet(
        "mg",
        "mg combined (v4+v5+v6)",
        mg_rows,
        "v4+v5+v6 train jsonl",
    )

    og_m = continuity_metrics(og_set)
    v4_m = continuity_metrics(v4_set)
    v5_m = continuity_metrics(v5_set)
    v6_m = continuity_metrics(v6_set)
    mg_m = continuity_metrics(mg_set)

    og_ps = [r.get("problem_statement") or "" for r in og_rows]
    mg_ps = [r.get("problem_statement") or "" for r in mg_rows]
    cross_m2o = nearest_neighbour_stats(mg_ps, og_ps)
    cross_o2m = nearest_neighbour_stats(og_ps, mg_ps)

    mg_high_nn: list[dict[str, Any]] = []
    for row, score in zip(mg_rows, cross_m2o["scores"], strict=True):
        if score > 0.6:
            corpus = "v4" if row in v4_rows else "v5" if row in v5_rows else "v6"
            mg_high_nn.append(
                {
                    "instance_id": row["instance_id"],
                    "corpus": corpus,
                    "nn_jaccard": round(score, 4),
                }
            )
    mg_high_nn.sort(key=lambda r: -r["nn_jaccard"])
    # Remove raw scores from serialised cross stats.
    cross_m2o_out = {k: v for k, v in cross_m2o.items() if k != "scores"}
    cross_o2m_out = {k: v for k, v in cross_o2m.items() if k != "scores"}

    outcome_map = load_mg_outcome_map()
    og_outcomes = outcome_distribution(og_rows, use_seed=False, outcome_map={})
    mg_outcomes = outcome_distribution(mg_rows, use_seed=True, outcome_map=outcome_map)
    v4_outcomes = outcome_distribution(v4_rows, use_seed=True, outcome_map=outcome_map)
    v5_outcomes = outcome_distribution(v5_rows, use_seed=True, outcome_map=outcome_map)
    v6_outcomes = outcome_distribution(v6_rows, use_seed=True, outcome_map=outcome_map)

    table = np.array(
        [
            [og_outcomes.get(cat, 0) for cat in OUTCOME_CATEGORIES],
            [mg_outcomes.get(cat, 0) for cat in OUTCOME_CATEGORIES],
        ]
    )
    chi2_stat, chi2_df, chi2_p = chi2_independence(table)

    ps_probe = distinguishability_probe(og_rows, mg_rows, "problem_statement")
    eval_probe = distinguishability_probe(og_rows, mg_rows, "evaluator_code")
    traj = trajectory_lengths(MG_TRAJECTORIES)

    verdicts = build_verdict(
        og_m,
        mg_m,
        cross_m2o_out,
        mg_high_nn,
        og_outcomes,
        mg_outcomes,
        ps_probe,
        eval_probe,
    )

    stats = {
        "og_v3": og_m,
        "mg_combined": mg_m,
        "mg_v4": v4_m,
        "mg_v5": v5_m,
        "mg_v6": v6_m,
        "cross_set_jaccard": {
            "mg_to_og": cross_m2o_out,
            "og_to_mg": cross_o2m_out,
            "mg_tasks_above_0.6": mg_high_nn,
        },
        "outcome_composition": {
            "og_inferred": dict(og_outcomes),
            "mg_combined": dict(mg_outcomes),
            "mg_v4": dict(v4_outcomes),
            "mg_v5": dict(v5_outcomes),
            "mg_v6": dict(v6_outcomes),
            "chi2_og_vs_mg": {
                "chi2": round(chi2_stat, 4),
                "df": chi2_df,
                "p_value_approx": round(chi2_p, 6),
            },
        },
        "distinguishability_probe": {
            "problem_statement": ps_probe,
            "evaluator_code": eval_probe,
        },
        "trajectory_lengths": traj,
        "og_rollout_cited": OG_ROLLOUT_CITED,
        "og_jaccard_cited": OG_JACCARD_CITED,
        "og_trajectory_median_cited": OG_TRAJECTORY_MEDIAN_CITED,
        "verdicts": verdicts,
    }

    ctx = {
        "og": og_m,
        "mg_combined": mg_m,
        "mg_v4": v4_m,
        "mg_v5": v5_m,
        "mg_v6": v6_m,
        "cross_mg_to_og": cross_m2o_out,
        "cross_og_to_mg": cross_o2m_out,
        "mg_high_nn": mg_high_nn,
        "chi2": {"chi2": chi2_stat, "df": chi2_df, "p_value": chi2_p},
        "ps_probe": ps_probe,
        "eval_probe": eval_probe,
        "trajectories": traj,
        "verdicts": verdicts,
        "og_outcomes": og_outcomes,
        "mg_outcomes": mg_outcomes,
        "v4_outcomes": v4_outcomes,
        "v5_outcomes": v5_outcomes,
        "v6_outcomes": v6_outcomes,
    }

    report_path = OUT_DIR / "report.md"
    stats_path = OUT_DIR / "stats.json"
    report_path.write_text(render_report(ctx), encoding="utf-8")
    stats_path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {report_path}")
    print(f"Wrote {stats_path}")


if __name__ == "__main__":
    main()
