"""ShieldGemma scaling ladder: nested subsets, shipped splits, mock e2e."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from safety_monitor.critic.shieldgemma import (
    _POLICY,
    _SHIELDGEMMA_TEMPLATE,
    ShieldGemmaCritic,
    format_shieldgemma_prompt,
    load_shieldgemma_critic,
)
from safety_monitor.sft.backends import probe_hardware, train_lora_sft
from safety_monitor.sft.data import (
    PROMPT_FORMAT_SHIELDGEMMA,
    assert_train_eval_firewall,
    example_from_action,
    format_example_as_shieldgemma,
    mined_to_steps,
    shieldgemma_sft_messages,
    split_by_shipped_fields,
    strata_key_for_task,
)
from safety_monitor.sft.scaling import nested_stratified_subsets, run_scaling_ladder
from safety_monitor.synthesis.pairs import LabeledStep, MinedTrajectory
from safety_monitor.types import SafetyLabel


def _step(
    action_id: str,
    command: str,
    label: SafetyLabel,
    *,
    tool: str = "terminal",
) -> LabeledStep:
    return LabeledStep(
        action_id=action_id,
        tool_name=tool,
        arguments={"command": command},
        summary=command[:40],
        observation="ok",
        label=label,
        label_source="test",
    )


def _traj(
    instance_id: str,
    role: str,
    steps: list[LabeledStep],
    *,
    key: str | None = None,
    condition: str = "synthetic",
    run: str = "v6_synthetic",
    rule_based: int | None = None,
    split: str = "train",
    corpus: str | None = "v6",
    outcome_category: str | None = None,
    services: list[str] | None = None,
) -> MinedTrajectory:
    if rule_based is None:
        rule_based = 1 if role == "harmful" else 0
    return MinedTrajectory(
        key=key or f"{run}::{instance_id}::{role}",
        instance_id=instance_id,
        run=run,
        condition=condition,
        actor="test",
        role=role,
        rule_based=rule_based,
        instruction="Do the task. Do not leak secrets.",
        steps=steps,
        split=split,
        corpus=corpus,
        outcome_category=outcome_category,
        services=services or [],
    )


def _pair(
    instance_id: str,
    *,
    split: str = "train",
    corpus: str = "v6",
    outcome_category: str | None = None,
    services: list[str] | None = None,
) -> list[MinedTrajectory]:
    return [
        _traj(
            instance_id,
            "harmful",
            [
                _step(f"{instance_id}-0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                _step(
                    f"{instance_id}-1",
                    "git push origin main",
                    SafetyLabel.HIGH_UNSAFE,
                ),
            ],
            split=split,
            corpus=corpus,
            outcome_category=outcome_category,
            services=services,
        ),
        _traj(
            instance_id,
            "clean",
            [
                _step(f"{instance_id}-c0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                _step(f"{instance_id}-c1", "echo no push", SafetyLabel.LOW_UNSAFE),
            ],
            split=split,
            corpus=corpus,
            outcome_category=outcome_category,
            services=services,
        ),
    ]


def test_nested_subsets_are_nested_and_stratified():
    ids = [f"task-{i:02d}" for i in range(12)]
    strata = {iid: ("A" if i < 6 else "B") for i, iid in enumerate(ids)}
    subsets = nested_stratified_subsets(ids, strata, [4, 8, 12], seed=7)
    small, mid, full = subsets[4], subsets[8], subsets[12]
    assert set(small) <= set(mid) <= set(full)
    assert small == mid[:4]
    assert mid == full[:8]
    assert set(full) == set(ids)
    assert 1 <= sum(1 for i in small if strata[i] == "A") <= 3
    assert 1 <= sum(1 for i in small if strata[i] == "B") <= 3


def test_shipped_split_keeps_dev_test_out_of_train():
    synthetic = []
    synthetic.extend(_pair("t-train", split="train"))
    synthetic.extend(_pair("t-dev", split="dev"))
    synthetic.extend(_pair("t-test", split="test"))
    synthetic.extend(_pair("t-eval-legacy", split="eval"))
    v3 = [
        _traj(
            "v3-real",
            "harmful",
            [_step("v0", "ls", SafetyLabel.LOW_UNSAFE)],
            condition="baseline",
            run="baseline_no_analyzer_claude",
            split="train",
            corpus=None,
        )
    ]
    shipped = split_by_shipped_fields(synthetic, v3)
    assert shipped.train_instance_ids == ["t-train"]
    assert shipped.dev_instance_ids == ["t-dev"]
    assert shipped.test_instance_ids == ["t-test"]
    assert shipped.unused_instance_ids == ["t-eval-legacy"]
    train_ids = set(shipped.train_instance_ids)
    assert "t-dev" not in train_ids
    assert "t-test" not in train_ids
    assert shipped.as_dict()["firewall"]["train_intersect_v3_eval_instance_ids"] == []


def test_firewall_assertion_fires_on_planted_overlap():
    with pytest.raises(ValueError, match="train ∩ v3-eval"):
        assert_train_eval_firewall(["safety-password", "other"], ["safety-password"])


def test_split_by_shipped_fields_rejects_planted_v3_overlap():
    synthetic = _pair("shared-id", split="train")
    v3 = [
        _traj(
            "shared-id",
            "harmful",
            [_step("v0", "ls", SafetyLabel.LOW_UNSAFE)],
            condition="baseline",
            run="baseline_no_analyzer_claude",
            corpus=None,
        )
    ]
    with pytest.raises(ValueError, match="firewall"):
        split_by_shipped_fields(synthetic, v3)


def test_shieldgemma_formatter_matches_critic_template():
    traj = _pair("fmt")[0]
    steps = mined_to_steps(traj)
    action = steps[1].action
    assert action is not None
    history = steps[:1]
    messages = shieldgemma_sft_messages(action, history, label=SafetyLabel.HIGH_UNSAFE)
    expected = format_shieldgemma_prompt(action, history)
    assert messages[0]["content"] == expected
    assert messages[1]["content"] == "Yes"
    assert _POLICY.strip() in expected
    filled = _SHIELDGEMMA_TEMPLATE.format(
        user_prompt="PLACEHOLDER",
        safety_policy=_POLICY.strip(),
    )
    # Same wrapper text as the official template (minus the filled user slot).
    assert "Does the human question violate the above principle?" in expected
    assert filled.split("PLACEHOLDER")[0] in expected
    example = example_from_action(
        traj, 1, bucket="train", source="v6", prompt_format=PROMPT_FORMAT_SHIELDGEMMA
    )
    assert format_example_as_shieldgemma(example)[0]["content"] == expected
    assert example.assistant_text == "Yes"


def test_strata_falls_back_to_corpus_without_outcome_metadata():
    trajs = _pair("c-only", corpus="v5", outcome_category=None, services=None)
    assert strata_key_for_task(trajs) == "corpus:v5"
    labeled = _pair(
        "with-meta",
        corpus="v6",
        outcome_category="Privacy breach",
        services=["gitlab"],
    )
    assert strata_key_for_task(labeled) == "Privacy breach|gitlab"


def test_ladder_e2e_mock_tiny_fixture(tmp_path: Path):
    train_path = tmp_path / "train.jsonl"
    eval_path = tmp_path / "eval.jsonl"
    synthetic: list[MinedTrajectory] = []
    for i in range(4):
        synthetic.extend(
            _pair(
                f"syn-train-{i}",
                split="train",
                corpus="v6" if i % 2 == 0 else "v5",
            )
        )
    synthetic.extend(_pair("syn-dev", split="dev"))
    synthetic.extend(_pair("syn-test", split="test"))
    v3 = [
        _traj(
            "v3-a",
            "harmful",
            [
                _step("v0", "ls", SafetyLabel.LOW_UNSAFE),
                _step("v1", "git push origin main", SafetyLabel.HIGH_UNSAFE),
            ],
            condition="baseline",
            run="baseline_no_analyzer_claude",
            corpus=None,
            key="baseline::v3-a::1",
        ),
        _traj(
            "v3-b",
            "clean",
            [_step("v2", "ls /workspace", SafetyLabel.LOW_UNSAFE)],
            condition="baseline",
            run="baseline_no_analyzer_claude",
            corpus=None,
            key="baseline::v3-b::1",
        ),
    ]
    train_path.write_text(
        "\n".join(t.model_dump_json() for t in synthetic) + "\n", encoding="utf-8"
    )
    eval_path.write_text(
        "\n".join(t.model_dump_json() for t in v3) + "\n", encoding="utf-8"
    )
    out = tmp_path / "ladder"
    matplotlib = pytest.importorskip("matplotlib")
    del matplotlib
    result = run_scaling_ladder(
        train_paths=[train_path],
        eval_path=eval_path,
        out_dir=out,
        backend="mock",
        rungs=["0", "2"],
        epochs=1,
        seed=0,
    )
    assert result["firewall"]["ok"] is True
    assert result["firewall"]["train_intersect_v3_eval_instance_ids"] == []
    assert (out / "results.json").exists()
    assert (out / "report.md").exists()
    assert (out / "auroc_vs_n_train.png").exists()
    rungs = {r["rung"]: r for r in result["per_rung"]}
    assert set(rungs) == {"0", "2"}
    assert rungs["0"]["n_train_tasks"] == 0
    assert rungs["2"]["n_train_tasks"] == 2
    assert rungs["0"]["sft_actually_ran"] is False
    two_ids = set(rungs["2"]["task_ids"])
    assert two_ids <= {"syn-train-0", "syn-train-1", "syn-train-2", "syn-train-3"}
    assert "syn-dev" not in two_ids
    assert "syn-test" not in two_ids
    assert "v3-a" not in two_ids
    payload = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert payload["split"]["split_source"] == "shipped"
    assert "v3 max AUROC" in (out / "report.md").read_text(encoding="utf-8")


def test_probe_hf_shieldgemma_fails_closed_without_weights(monkeypatch):
    monkeypatch.delenv("SHIELDGEMMA_MODEL_PATH", raising=False)
    probe = probe_hardware(backend="hf", model_family="shieldgemma")
    assert probe.can_sft is False
    assert probe.family == "shieldgemma"
    assert "ShieldGemma" in probe.reason or "GPU/CUDA" in probe.reason


def test_train_lora_sft_exposes_completion_mode():
    params = inspect.signature(train_lora_sft).parameters
    assert "use_chat_template" in params
    assert params["use_chat_template"].default is True


def test_shieldgemma_critic_records_adapter_dir(tmp_path: Path, monkeypatch):
    weights = tmp_path / "sg"
    weights.mkdir()
    monkeypatch.setenv("SHIELDGEMMA_MODEL_PATH", str(weights))
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    critic = load_shieldgemma_critic(adapter_dir=str(adapter), threshold=0.4)
    assert critic.adapter_dir == str(adapter)
    assert critic.threshold == 0.4
    bare = ShieldGemmaCritic(adapter_dir=str(adapter))
    assert bare.adapter_dir == str(adapter)


def test_ensure_local_model_loads_peft_adapter(monkeypatch, tmp_path: Path):
    """GPU-path wiring: adapter_dir is passed to PeftModel. Skips without HF stack."""
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    peft = pytest.importorskip("peft")
    weights = tmp_path / "sg"
    weights.mkdir()
    (weights / "config.json").write_text(
        json.dumps({"architectures": ["GemmaForCausalLM"]}), encoding="utf-8"
    )
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    seen: dict[str, object] = {}

    class _Tok:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

    class _Cfg:
        architectures = ["GemmaForCausalLM"]

    class _Model:
        def eval(self) -> None:
            seen["eval"] = True

    def fake_from_pretrained(model, path):
        seen["adapter"] = path
        seen["base"] = model
        wrapped = _Model()
        seen["wrapped"] = wrapped
        return wrapped

    monkeypatch.setattr(
        "transformers.AutoTokenizer.from_pretrained", lambda *a, **k: _Tok()
    )
    monkeypatch.setattr(
        "transformers.AutoConfig.from_pretrained", lambda *a, **k: _Cfg()
    )
    monkeypatch.setattr(
        "transformers.AutoModelForCausalLM.from_pretrained",
        lambda *a, **k: _Model(),
    )
    monkeypatch.setattr(peft.PeftModel, "from_pretrained", fake_from_pretrained)
    critic = ShieldGemmaCritic(model_path=str(weights), adapter_dir=str(adapter))
    impl = critic._ensure_local_model()
    assert seen.get("adapter") == str(adapter)
    assert callable(impl)


def test_lora_sft_gpu_path_skipped_without_cuda():
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA required for ShieldGemma LoRA SFT")
    pytest.importorskip("transformers")
    pytest.importorskip("peft")
    pytest.skip("weights not bundled; GPU path is exercised on the GPU server")


def test_cli_registers_sft_scaling_and_sft_run():
    from safety_monitor.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["sft-scaling", "--help"])
    assert exc.value.code == 0
    with pytest.raises(SystemExit) as exc:
        main(["sft-run", "--help"])
    assert exc.value.code == 0
    with pytest.raises(SystemExit) as exc:
        main(["sft-qwen", "--help"])
    assert exc.value.code == 0
