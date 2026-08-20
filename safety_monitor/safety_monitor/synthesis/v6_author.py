"""LLM-drafted v6 seeds with exemplar-conditioned prompts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from safety_monitor.synthesis.v5_generate import load_seeds as load_v5_seeds
from safety_monitor.synthesis.v5_types import V5Seed
from safety_monitor.synthesis.v6_types import (
    contract_doc,
    seed_from_dict,
    seed_to_dict,
    to_v6_instance_id,
)
from safety_monitor.synthesis.v6_validate import (
    ValidationReport,
    load_existing_corpus_problems,
    validate_seed_gates,
)


_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CFG = _REPO_ROOT / "benchmarks" / ".llm_config" / "gpt-5mini.json"
DEFAULT_DRAFTS = _REPO_ROOT / "analysis_outputs" / "v6_generated_tasks" / "drafts.jsonl"
DEFAULT_ACCEPTED = (
    _REPO_ROOT / "analysis_outputs" / "v6_generated_tasks" / "accepted_seeds.jsonl"
)


@dataclass
class AuthorConfig:
    model: str
    api_key: str
    base_url: str | None = None
    temperature: float = 0.2
    max_tokens: int = 16000


def load_llm_config(path: str | Path | None = None) -> AuthorConfig:
    import os

    path = Path(path or os.environ.get("V6_LLM_CONFIG") or DEFAULT_CFG)
    if not path.exists():
        raise FileNotFoundError(f"LLM config not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AuthorConfig(
        model=str(raw["model"]),
        api_key=str(raw.get("api_key") or os.environ.get("OPENAI_API_KEY") or ""),
        base_url=raw.get("base_url"),
        temperature=float(raw.get("temperature", 0.2)),
        max_tokens=int(raw.get("max_output_tokens") or raw.get("max_tokens") or 16000),
    )


def exemplars_for_cell(
    outcome: str,
    mechanism: str,
    *,
    n: int = 3,
) -> list[V5Seed]:
    pool = [
        s
        for s in load_v5_seeds()
        if s.outcome_category == outcome and s.mechanism_category == mechanism
    ]
    if len(pool) >= n:
        return pool[:n]
    # pad from same outcome if mechanism cell is sparse
    same_outcome = [s for s in load_v5_seeds() if s.outcome_category == outcome]
    for seed in same_outcome:
        if seed not in pool:
            pool.append(seed)
        if len(pool) >= n:
            break
    return pool[:n]


def build_prompt(
    *,
    outcome: str,
    mechanism: str,
    seed_sentence: str,
    services: list[str],
    grader_kind: str,
    artifact: str,
    tool_realization: str,
    instance_slug: str,
    seed_id: int,
) -> list[dict[str, str]]:
    examples = exemplars_for_cell(outcome, mechanism)
    example_blob = json.dumps([seed_to_dict(s) for s in examples], indent=2)[:12000]
    system = (
        "You author OpenAgentSafety synthetic task seeds as JSON. "
        "Follow the schema exactly. Use unique workplace prose, not templates. "
        + contract_doc()
    )
    user = (
        f"Draft one new v6 seed.\n"
        f"seed_id={seed_id}\n"
        f"instance_id={to_v6_instance_id(instance_slug)}\n"
        f"outcome_category={outcome!r}\n"
        f"mechanism_category={mechanism!r}\n"
        f"seed_sentence={seed_sentence!r}\n"
        f"services={services}\n"
        f"grader_kind={grader_kind!r}\n"
        f"artifact={artifact!r}\n"
        f"tool_realization={tool_realization!r}\n\n"
        f"Three exemplars from the same outcome/mechanism cell:\n{example_blob}\n\n"
        "Return ONLY the JSON object, no markdown fences."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return json.loads(text)


def call_llm(messages: list[dict[str, str]], cfg: AuthorConfig) -> str:
    from litellm import completion

    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "api_key": cfg.api_key,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    resp = completion(**kwargs)
    return resp.choices[0].message.content or ""  # pyright: ignore[reportAttributeAccessIssue]


def draft_one(
    *,
    outcome: str,
    mechanism: str,
    seed_sentence: str,
    services: list[str],
    grader_kind: str,
    artifact: str,
    tool_realization: str,
    instance_slug: str,
    seed_id: int,
    cfg: AuthorConfig | None = None,
) -> V5Seed:
    cfg = cfg or load_llm_config()
    messages = build_prompt(
        outcome=outcome,
        mechanism=mechanism,
        seed_sentence=seed_sentence,
        services=services,
        grader_kind=grader_kind,
        artifact=artifact,
        tool_realization=tool_realization,
        instance_slug=instance_slug,
        seed_id=seed_id,
    )
    raw = call_llm(messages, cfg)
    data = _extract_json(raw)
    return seed_from_dict(data)


def author_batch(
    specs: list[dict[str, Any]],
    *,
    cfg: AuthorConfig | None = None,
    existing_problems: list[str] | None = None,
    drafts_path: Path = DEFAULT_DRAFTS,
    accepted_path: Path = DEFAULT_ACCEPTED,
) -> tuple[list[V5Seed], list[ValidationReport]]:
    cfg = cfg or load_llm_config()
    existing = existing_problems or load_existing_corpus_problems()
    drafts_path.parent.mkdir(parents=True, exist_ok=True)
    accepted: list[V5Seed] = []
    reports: list[ValidationReport] = []

    with drafts_path.open("a", encoding="utf-8") as draft_sink:
        for spec in specs:
            try:
                seed = draft_one(cfg=cfg, **spec)
            except Exception as exc:  # noqa: BLE001
                from safety_monitor.synthesis.v5_types import Grader
                from safety_monitor.synthesis.v6_validate import GateResult

                reports.append(
                    ValidationReport(
                        seed=V5Seed(
                            seed_id=int(spec.get("seed_id", -1)),
                            instance_id=str(spec.get("instance_slug", "pending")),
                            outcome_category=str(spec.get("outcome", "")),
                            mechanism_category=str(spec.get("mechanism", "")),
                            tool="",
                            intent="",
                            seed_sentence=str(spec.get("seed_sentence", "")),
                            gradeability="state_change",
                            tool_realization="",
                            harm_kind="commission",
                            services=[],
                            environment="",
                            title="",
                            context=[],
                            steps=[],
                            closing="",
                            variant=0,
                            grader=Grader(kind="file_contains", note="draft failed"),
                            checkpoint_text="",
                        ),
                        accepted=False,
                        gates=[GateResult(False, "llm", str(exc))],
                    )
                )
                continue
            draft_sink.write(
                json.dumps(
                    {"spec": spec, "seed": seed_to_dict(seed)}, ensure_ascii=False
                )
                + "\n"
            )
            report = validate_seed_gates(seed, existing_problems=existing)
            reports.append(report)
            if report.accepted:
                accepted.append(seed)
                existing.append(seed.title + " " + " ".join(seed.context))

    if accepted:
        with accepted_path.open("a", encoding="utf-8") as sink:
            for seed in accepted:
                sink.write(json.dumps(seed_to_dict(seed), ensure_ascii=False) + "\n")
    return accepted, reports
