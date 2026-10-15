"""Coverage briefs for v8: one cell of the outcome x mechanism x intent x harm grid plus a seed topic."""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

from pydantic import BaseModel

from safety_monitor.synthesis.v8_prompts import Vars


class Brief(BaseModel):
    brief_id: str
    seed_topic: str
    outcome_category: str
    mechanism_category: str  # full "A  Authorization / access: ..." line
    intent: str
    harm_kind: str


def enumerate_briefs(
    vars: Vars, topics: list[str], n: int, seed: int = 0
) -> list[Brief]:
    """``n`` briefs, uniform over the grid.

    The grid is walked in shuffled passes (each cell once per pass), so every cell
    is used ``n // len(grid)`` or one more times. Topics are drawn round-robin
    from a shuffled topic list.
    """
    rng = random.Random(seed)
    grid = list(
        itertools.product(vars.outcomes, vars.mechanisms, vars.intents, vars.harm_kinds)
    )
    shuffled_topics = list(topics)
    rng.shuffle(shuffled_topics)
    cells: list[tuple[str, str, str, str]] = []
    while len(cells) < n:
        batch = list(grid)
        rng.shuffle(batch)
        cells.extend(batch)
    return [
        Brief(
            brief_id=f"b{idx:04d}",
            seed_topic=shuffled_topics[idx % len(shuffled_topics)],
            outcome_category=o,
            mechanism_category=m,
            intent=i,
            harm_kind=h,
        )
        for idx, (o, m, i, h) in enumerate(cells[:n])
    ]


def write_briefs(briefs: list[Brief], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(b.model_dump_json() + "\n" for b in briefs), encoding="utf-8"
    )


def read_briefs(path: Path) -> list[Brief]:
    return [
        Brief.model_validate(json.loads(line))
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
