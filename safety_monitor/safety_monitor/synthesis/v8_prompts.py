"""Load the user's v8 prompt files and fill their placeholders.

The prompt files under ``analysis_outputs/v8_tasks/prompts`` are read verbatim
and never edited. Placeholders are single-brace names (``{seed_topic}``); the
prompts also contain literal JSON braces, so substitution is a single regex
pass over the known names only (never ``str.format``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from safety_monitor.analysis.credit_assignment import RAS_ROOT


if TYPE_CHECKING:
    from safety_monitor.synthesis.v8_briefs import Brief

PROMPTS_DIR = RAS_ROOT / "analysis_outputs" / "v8_tasks" / "prompts"
VARS_PATH = PROMPTS_DIR / "vars.md"
POLICIES_PATH = PROMPTS_DIR / "policies.md"
SEED_TOPICS_PATH = PROMPTS_DIR / "seed_topics.md"
INPUT_PROMPT_PATH = PROMPTS_DIR / "input_prompt.md"
REVIEW_PROMPT_PATH = PROMPTS_DIR / "review_prompt.md"

GENERATE_USER = "Produce the task now."
GENERATE_PLACEHOLDERS = (
    "seed_topic",
    "outcome_category",
    "mechanism_category",
    "intent",
    "harm_kind",
    "TOOL_SURFACE",
    "governing_policy",
)
REVIEW_PLACEHOLDERS = ("scenario_json", "TOOL_SURFACE", "governing_policy")

_HEADER_RE = re.compile(r"^([A-Z][A-Z_]+)\b.*:\s*$")


@dataclass(frozen=True)
class Vars:
    tool_surface: str
    outcomes: list[str]
    mechanisms: list[str]  # full lines, e.g. "A  Authorization / access: ..."
    intents: list[str]
    harm_kinds: list[str]  # names only: "commission", "omission"
    harm_kind_lines: dict[str, str]  # name -> full definition line


def _sections(text: str) -> dict[str, list[str]]:
    """Header line ``NAME ...:`` followed by body lines up to the next blank line."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = _HEADER_RE.match(line.strip())
        if current is None and m:
            current = str(m.group(1))
            out[current] = []
            continue
        if current is None:
            continue
        if not line.strip():
            current = None
            continue
        out[current].append(line.rstrip())
    return out


def _split_bars(lines: list[str]) -> list[str]:
    return [p.strip() for p in " ".join(lines).split("|") if p.strip()]


def load_vars(path: Path = VARS_PATH) -> Vars:
    sec = _sections(Path(path).read_text(encoding="utf-8"))
    for name in (
        "TOOL_SURFACE",
        "OUTCOME_CATEGORIES",
        "MECHANISM_CATEGORIES",
        "INTENT",
        "HARM_KIND",
    ):
        if not sec.get(name):
            raise ValueError(f"{path}: section {name} missing or empty")
    harm_lines = [ln.strip() for ln in sec["HARM_KIND"]]
    harm_kind_lines = {ln.split()[0]: re.sub(r"\s+", " ", ln) for ln in harm_lines}
    return Vars(
        tool_surface="\n".join(sec["TOOL_SURFACE"]),
        outcomes=_split_bars(sec["OUTCOME_CATEGORIES"]),
        mechanisms=[ln.strip() for ln in sec["MECHANISM_CATEGORIES"]],
        intents=_split_bars(sec["INTENT"]),
        harm_kinds=list(harm_kind_lines),
        harm_kind_lines=harm_kind_lines,
    )


def load_policies(
    path: Path = POLICIES_PATH, required: list[str] | None = None
) -> dict[str, str]:
    """``## <category>`` heading followed by the policy sentence."""
    policies: dict[str, str] = {}
    current: str | None = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            policies[current] = ""
        elif current is not None and line.strip():
            policies[current] = (policies[current] + " " + line.strip()).strip()
    empty = [k for k, v in policies.items() if not v]
    missing = [c for c in (required or []) if c not in policies]
    if missing or empty:
        raise ValueError(f"{path}: no policy for {missing + empty}")
    return policies


def load_seed_topics(path: Path = SEED_TOPICS_PATH) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    topics = [
        ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not topics:
        raise ValueError(f"{path}: no seed topics")
    return topics


def fill(template: str, values: dict[str, str]) -> str:
    """Replace ``{name}`` for each key in one pass; every key must occur in the template."""
    absent = [k for k in values if "{" + k + "}" not in template]
    if absent:
        raise ValueError(f"placeholders not in template: {absent}")
    pattern = re.compile(r"\{(" + "|".join(re.escape(k) for k in values) + r")\}")
    return pattern.sub(lambda m: values[m.group(1)], template)


def render_generate(
    brief: Brief, vars: Vars, policy: str, *, template_path: Path = INPUT_PROMPT_PATH
) -> list[dict[str, str]]:
    system = fill(
        Path(template_path).read_text(encoding="utf-8"),
        {
            "seed_topic": brief.seed_topic,
            "outcome_category": brief.outcome_category,
            "mechanism_category": brief.mechanism_category,
            "intent": brief.intent,
            "harm_kind": vars.harm_kind_lines.get(brief.harm_kind, brief.harm_kind),
            "TOOL_SURFACE": vars.tool_surface,
            "governing_policy": policy,
        },
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": GENERATE_USER},
    ]


def render_review(
    candidate_json: str,
    vars: Vars,
    policy: str,
    *,
    template_path: Path = REVIEW_PROMPT_PATH,
) -> list[dict[str, str]]:
    content = fill(
        Path(template_path).read_text(encoding="utf-8"),
        {
            "scenario_json": candidate_json,
            "TOOL_SURFACE": vars.tool_surface,
            "governing_policy": policy,
        },
    )
    return [{"role": "user", "content": content}]


def render_appendix(out_path: Path, prompts_dir: Path = PROMPTS_DIR) -> Path:
    """Concatenate the prompt inputs verbatim under headings (paper appendix)."""
    parts = [
        ("Coverage variables (vars.md)", "vars.md"),
        ("Governing policies (policies.md)", "policies.md"),
        ("Seed topics (seed_topics.md)", "seed_topics.md"),
        ("Generation prompt (input_prompt.md)", "input_prompt.md"),
        ("Review prompt (review_prompt.md)", "review_prompt.md"),
    ]
    chunks = ["# Appendix: v8 task-generation prompts\n"]
    for title, name in parts:
        body = (Path(prompts_dir) / name).read_text(encoding="utf-8").rstrip("\n")
        chunks.append(f"## {title}\n\n````text\n{body}\n````\n")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(chunks), encoding="utf-8")
    return out_path
