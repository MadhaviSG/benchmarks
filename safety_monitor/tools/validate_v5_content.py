#!/usr/bin/env python3
"""Contract check for an authored ``v5_content_*`` module.

Usage (from the ``safety_monitor`` directory):

    PYTHONPATH=. ../benchmarks/.venv/bin/python tools/validate_v5_content.py v5_content_b

Exits non-zero and prints every violation if the module does not satisfy
:mod:`safety_monitor.synthesis.v5_types`. Also prints a local prose-diversity
figure so an author can see whether their own tasks read as templated.
"""

from __future__ import annotations

import importlib
import re
import sys

from safety_monitor.synthesis.v5_types import V5Seed, validate_all


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def _mean_pairwise_jaccard(texts: list[str]) -> float:
    toks = [_tokens(t) for t in texts]
    if len(toks) < 2:
        return float("nan")
    total = 0.0
    count = 0
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            union = toks[i] | toks[j]
            total += (len(toks[i] & toks[j]) / len(union)) if union else 0.0
            count += 1
    return total / count


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    name = argv[1]
    module = importlib.import_module(f"safety_monitor.synthesis.{name}")
    seeds: list[V5Seed] = list(module.SEEDS)
    problems = validate_all(seeds)
    for problem in problems:
        print(f"FAIL {problem}")
    blobs = [" ".join(s.context + s.steps + [s.title, s.closing]) for s in seeds]
    jaccard = _mean_pairwise_jaccard(blobs)
    print(f"\n{name}: {len(seeds)} seeds, {len(problems)} contract violations")
    print(
        f"mean pairwise Jaccard of scenario prose within this module: {jaccard:.3f} (target <= 0.09)"
    )
    words = [len(b.split()) for b in blobs]
    if words:
        print(f"scenario prose word counts: min {min(words)}, max {max(words)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
