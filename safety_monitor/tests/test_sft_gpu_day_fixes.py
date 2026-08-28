"""GPU-day SFT fixes: batch flags, loud HF fallback, report config, batched eval."""

from __future__ import annotations

from pathlib import Path

import pytest
from safety_monitor.critic.shieldgemma import (
    ShieldGemmaCritic,
    score_causal_yes_no,
    score_causal_yes_no_batch,
)
from safety_monitor.sft.backends import HardwareProbe, HfUnavailableError
from safety_monitor.sft.experiment import run_experiment
from safety_monitor.sft.report import (
    format_sft_rerun_command,
    training_data_blurb,
    write_report,
)
from safety_monitor.sft.scaling import run_scaling_ladder, score_trajectories
from safety_monitor.synthesis.pairs import LabeledStep, MinedTrajectory
from safety_monitor.types import SafetyLabel


def _step(action_id: str, command: str, label: SafetyLabel) -> LabeledStep:
    return LabeledStep(
        action_id=action_id,
        tool_name="terminal",
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
    condition: str = "synthetic",
    run: str = "v6_synthetic",
    split: str = "train",
    corpus: str | None = "v6",
) -> MinedTrajectory:
    return MinedTrajectory(
        key=f"{run}::{instance_id}::{role}",
        instance_id=instance_id,
        run=run,
        condition=condition,
        actor="test",
        role=role,
        rule_based=1 if role == "harmful" else 0,
        instruction="Do the task.",
        steps=steps,
        split=split,
        corpus=corpus,
    )


def _pair(instance_id: str, *, split: str = "train") -> list[MinedTrajectory]:
    return [
        _traj(
            instance_id,
            "harmful",
            [
                _step(f"{instance_id}-0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                _step(
                    f"{instance_id}-1", "git push origin main", SafetyLabel.HIGH_UNSAFE
                ),
            ],
            split=split,
        ),
        _traj(
            instance_id,
            "clean",
            [
                _step(f"{instance_id}-c0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                _step(f"{instance_id}-c1", "echo no push", SafetyLabel.LOW_UNSAFE),
            ],
            split=split,
        ),
    ]


def _failed_probe(**kwargs) -> HardwareProbe:
    return HardwareProbe(
        can_sft=False,
        backend="hf",
        reason="HuggingFace backend requested but missing: GPU/CUDA, local Qwen weights",
        **kwargs,
    )


def test_sft_run_flags_thread_to_experiment(monkeypatch, tmp_path: Path):
    seen: dict = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {"sft_actually_ran": False, "backend": "mock"}

    monkeypatch.setattr("safety_monitor.sft.experiment.run_experiment", fake_run)
    from safety_monitor.__main__ import main

    rc = main(
        [
            "sft-run",
            "--backend",
            "mock",
            "--batch-size",
            "4",
            "--grad-accum",
            "2",
            "--eval-batch-size",
            "8",
            "--strict",
            "--train",
            str(tmp_path / "train.jsonl"),
            "--eval",
            str(tmp_path / "eval.jsonl"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 0
    assert seen["batch_size"] == 4
    assert seen["grad_accum"] == 2
    assert seen["eval_batch_size"] == 8
    assert seen["strict"] is True
    assert seen["backend"] == "mock"


def test_sft_scaling_flags_thread_to_ladder(monkeypatch, tmp_path: Path):
    seen: dict = {}

    def fake_ladder(**kwargs):
        seen.update(kwargs)
        return {
            "experiment": "shieldgemma-sft-scaling",
            "sft_actually_ran": False,
            "backend": "mock",
            "rungs": [],
            "firewall": {},
        }

    monkeypatch.setattr("safety_monitor.sft.scaling.run_scaling_ladder", fake_ladder)
    from safety_monitor.__main__ import main

    rc = main(
        [
            "sft-scaling",
            "--backend",
            "mock",
            "--batch-size",
            "8",
            "--grad-accum",
            "1",
            "--eval-batch-size",
            "16",
            "--strict",
            "--train",
            str(tmp_path / "train.jsonl"),
            "--eval",
            str(tmp_path / "eval.jsonl"),
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 0
    assert seen["batch_size"] == 8
    assert seen["grad_accum"] == 1
    assert seen["eval_batch_size"] == 16
    assert seen["strict"] is True


def test_mock_run_records_nondefault_batch_flags(tmp_path: Path):
    train_path = tmp_path / "train.jsonl"
    eval_path = tmp_path / "eval.jsonl"
    synthetic = []
    for i in range(6):
        iid = f"safety-syn-{i:02d}"
        synthetic.extend(
            [
                _traj(
                    iid,
                    "harmful",
                    [
                        _step(f"{iid}-0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                        _step(
                            f"{iid}-1",
                            "git push origin main",
                            SafetyLabel.HIGH_UNSAFE,
                        ),
                    ],
                    run="v4_synthetic",
                    corpus="v4",
                ),
                _traj(
                    iid,
                    "clean",
                    [
                        _step(f"{iid}-c0", "ls /workspace", SafetyLabel.LOW_UNSAFE),
                        _step(f"{iid}-c1", "echo no", SafetyLabel.LOW_UNSAFE),
                    ],
                    run="v4_synthetic",
                    corpus="v4",
                ),
            ]
        )
    v3 = [
        _traj(
            "safety-v3-x",
            "harmful",
            [_step("v0", "ls", SafetyLabel.LOW_UNSAFE)],
            condition="baseline",
            run="baseline_no_analyzer_claude",
            corpus=None,
        )
    ]
    train_path.write_text(
        "\n".join(t.model_dump_json() for t in synthetic) + "\n", encoding="utf-8"
    )
    eval_path.write_text(
        "\n".join(t.model_dump_json() for t in v3) + "\n", encoding="utf-8"
    )
    result = run_experiment(
        train_paths=[train_path],
        eval_path=eval_path,
        out_dir=tmp_path / "out",
        backend="mock",
        batch_size=4,
        grad_accum=2,
        eval_batch_size=8,
        holdout_fraction=0.25,
    )
    assert result["run_config"]["batch_size"] == 4
    assert result["run_config"]["grad_accum"] == 2
    assert result["run_config"]["eval_batch_size"] == 8
    assert result["train_stats"]["batch_size"] == 4
    assert result["train_stats"]["grad_accum"] == 2
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "--batch-size 4" in report
    assert "--grad-accum 2" in report
    assert "--eval-batch-size 8" in report


def test_hf_fallback_prints_loud_banner(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(
        "safety_monitor.sft.experiment.probe_hardware",
        lambda **_k: _failed_probe(),
    )
    train_path = tmp_path / "train.jsonl"
    eval_path = tmp_path / "eval.jsonl"
    synthetic = []
    for i in range(6):
        iid = f"s-{i}"
        synthetic.extend(
            [
                _traj(
                    iid,
                    "harmful",
                    [
                        _step(
                            f"{iid}-1", "git push origin main", SafetyLabel.HIGH_UNSAFE
                        )
                    ],
                    run="v4_synthetic",
                    corpus="v4",
                ),
                _traj(
                    iid,
                    "clean",
                    [_step(f"{iid}-c", "ls", SafetyLabel.LOW_UNSAFE)],
                    run="v4_synthetic",
                    corpus="v4",
                ),
            ]
        )
    v3 = [
        _traj(
            "v3-a",
            "clean",
            [_step("v0", "ls", SafetyLabel.LOW_UNSAFE)],
            condition="baseline",
            run="baseline_no_analyzer_claude",
            corpus=None,
        )
    ]
    train_path.write_text(
        "\n".join(t.model_dump_json() for t in synthetic) + "\n", encoding="utf-8"
    )
    eval_path.write_text(v3[0].model_dump_json() + "\n", encoding="utf-8")
    result = run_experiment(
        train_paths=[train_path],
        eval_path=eval_path,
        out_dir=tmp_path / "out",
        backend="hf",
        strict=False,
        holdout_fraction=0.25,
    )
    err = capsys.readouterr().err
    assert "WARNING: SFT WILL NOT RUN — falling back to mock:" in err
    assert "=" * 20 in err
    assert result["sft_actually_ran"] is False
    assert result["backend"] == "mock"


def test_strict_raises_when_hf_probe_fails(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "safety_monitor.sft.experiment.probe_hardware",
        lambda **_k: _failed_probe(),
    )
    with pytest.raises(HfUnavailableError):
        run_experiment(
            train_paths=[tmp_path / "train.jsonl"],
            eval_path=tmp_path / "eval.jsonl",
            out_dir=tmp_path / "out",
            backend="hf",
            strict=True,
        )


def test_strict_cli_exits_nonzero(monkeypatch, tmp_path: Path):
    def boom(**_kwargs):
        raise HfUnavailableError("probe failed")

    monkeypatch.setattr("safety_monitor.sft.experiment.run_experiment", boom)
    from safety_monitor.__main__ import main

    rc = main(
        [
            "sft-run",
            "--backend",
            "hf",
            "--strict",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert rc != 0


def test_scaling_hf_hard_fails_instead_of_mock(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "safety_monitor.sft.scaling.probe_hardware",
        lambda **_k: HardwareProbe(
            can_sft=False,
            backend="hf",
            family="shieldgemma",
            reason="missing ShieldGemma weights",
        ),
    )
    train_path = tmp_path / "train.jsonl"
    eval_path = tmp_path / "eval.jsonl"
    train_path.write_text(
        "\n".join(t.model_dump_json() for t in _pair("t-train")) + "\n",
        encoding="utf-8",
    )
    eval_path.write_text(
        _traj(
            "v3-a",
            "clean",
            [_step("v0", "ls", SafetyLabel.LOW_UNSAFE)],
            condition="baseline",
            run="baseline_no_analyzer_claude",
            corpus=None,
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(HfUnavailableError):
        run_scaling_ladder(
            train_paths=[train_path],
            eval_path=eval_path,
            out_dir=tmp_path / "ladder",
            backend="hf",
            rungs=["0"],
        )
    with pytest.raises(HfUnavailableError):
        run_scaling_ladder(
            train_paths=[train_path],
            eval_path=eval_path,
            out_dir=tmp_path / "ladder-auto",
            backend="auto",
            rungs=["0"],
        )


def test_report_uses_shipped_split_not_stale_v4v5(tmp_path: Path):
    result = {
        "sft_actually_ran": True,
        "backend": "hf",
        "probe": {"cuda": True, "reason": "ok", "notes": []},
        "split": {
            "split_source": "shipped",
            "n_train_tasks": 455,
            "n_train_trajectories": 910,
            "n_holdout_tasks": 73,
            "n_holdout_trajectories": 146,
            "n_v3_eval_trajectories": 100,
            "n_v3_eval_tasks": 100,
            "salt": "v6-synthetic-500",
            "holdout_fraction": 0.0,
        },
        "example_counts": {},
        "metrics": {},
        "firewall": {"holdout_instance_overlap_with_train": []},
        "run_config": {
            "command": "sft-run",
            "train_paths": ["../analysis_outputs/synthetic_pairs/trajectories.jsonl"],
            "eval_path": "../analysis_outputs/critic_training_pairs/trajectories.jsonl",
            "out_dir": "../analysis_outputs/qwen_sft",
            "requested_backend": "hf",
            "backend": "hf",
            "model_path": "/models/qwen",
            "epochs": 2,
            "batch_size": 8,
            "grad_accum": 1,
            "eval_batch_size": 4,
            "use_shipped_splits": True,
            "strict": True,
        },
    }
    blurb = training_data_blurb(result)
    assert "shipped" in blurb
    assert "v4 + v5 + v6" in blurb
    assert "455 train tasks" in blurb
    assert "synthetic v4 + v5 trajectories only" not in blurb
    cmd = format_sft_rerun_command(result["run_config"])
    assert "sft-run" in cmd
    assert "sft-qwen" not in cmd
    assert "v4_synthetic_pairs" not in cmd
    assert "--batch-size 8" in cmd
    assert "--grad-accum 1" in cmd
    assert "--eval-batch-size 4" in cmd
    assert "--use-shipped-splits" in cmd
    assert "--backend hf" in cmd
    path = tmp_path / "report.md"
    write_report(path, result)
    text = path.read_text(encoding="utf-8")
    assert "v4_synthetic_pairs" not in text
    assert "sft-qwen" not in text
    assert "--batch-size 8" in text


def test_report_explicit_v4_v5_paths():
    result = {
        "split": {"split_source": "hashed", "salt": "qwen-sft"},
        "run_config": {
            "train_paths": [
                "../analysis_outputs/v4_synthetic_pairs/trajectories.jsonl",
                "../analysis_outputs/v5_synthetic_pairs/trajectories.jsonl",
            ]
        },
    }
    blurb = training_data_blurb(result)
    assert "synthetic v4 + v5" in blurb
    assert "v6" not in blurb.split("only")[0]


def test_batched_vs_unbatched_score_trajectories_identical():
    trajs = _pair("fmt")
    critic = ShieldGemmaCritic(
        score_fn=lambda prompt: (
            (0.91, "yes_prob=0.9100")
            if "git push" in prompt
            else (0.11, "yes_prob=0.1100")
        )
    )
    sequential = score_trajectories(critic, trajs, eval_batch_size=1)
    batched = score_trajectories(critic, trajs, eval_batch_size=8)
    seq_probs = [[a.yes_prob for a in row.actions] for row in sequential]
    batch_probs = [[a.yes_prob for a in row.actions] for row in batched]
    assert seq_probs == batch_probs
    assert seq_probs[0][1] == pytest.approx(0.91)
    assert seq_probs[0][0] == pytest.approx(0.11)


def test_causal_yes_no_batch_matches_sequential_and_one_forward():
    torch = pytest.importorskip("torch")

    class _Tok:
        pad_token = "pad"
        eos_token = "pad"
        pad_token_id = 0
        eos_token_id = 0

        def get_vocab(self):
            return {"Yes": 1, "No": 2, "pad": 0}

        def __call__(
            self,
            text,
            return_tensors="pt",
            truncation=True,
            max_length=2048,
            padding=False,
        ):
            strs = [text] if isinstance(text, str) else list(text)
            rows = []
            for t in strs:
                extra = 1 + (len(t) % 4)
                last = 1 + (len(t) % 3)
                rows.append([3, 4] + [last] * extra)
            width = max(len(r) for r in rows)
            ids = []
            mask = []
            for row in rows:
                pad = width - len(row)
                ids.append(row + [0] * pad)
                mask.append([1] * len(row) + [0] * pad)
            return {
                "input_ids": torch.tensor(ids, dtype=torch.long),
                "attention_mask": torch.tensor(mask, dtype=torch.long),
            }

        def decode(self, ids, skip_special_tokens=True):
            del ids, skip_special_tokens
            return "No"

    class _Model:
        def __init__(self):
            self.n_forwards = 0

        def parameters(self):
            yield torch.zeros(1)

        def __call__(self, input_ids, attention_mask=None, **_kwargs):
            self.n_forwards += 1
            batch, seq = input_ids.shape
            logits = torch.zeros(batch, seq, 8)
            logits[:, :, 1] = input_ids.float()
            logits[:, :, 2] = 1.0
            return type("Out", (), {"logits": logits})()

    model = _Model()
    tok = _Tok()
    prompts = ["short", "a somewhat longer prompt", "x"]
    sequential = [score_causal_yes_no(model, tok, p) for p in prompts]
    n_seq = model.n_forwards
    model.n_forwards = 0
    batched = score_causal_yes_no_batch(model, tok, prompts)
    assert model.n_forwards == 1
    assert n_seq == len(prompts)
    for (yes_a, raw_a), (yes_b, raw_b) in zip(sequential, batched):
        assert yes_a == pytest.approx(yes_b)
        assert raw_a == raw_b
