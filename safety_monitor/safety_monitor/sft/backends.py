"""Critic backends: local HuggingFace Qwen (preferred) and a CPU mock.

The mock exists so split/metrics can be exercised when there is no GPU and no
on-disk Qwen checkpoint. It is not a substitute for LoRA SFT.
"""

# pyright: reportMissingImports=false

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from safety_monitor.critic.prompted import (
    CompletionFn,
    PromptedSafetyCritic,
    parse_label,
)
from safety_monitor.types import SafetyLabel


# Prefer the smallest Instruct checkpoint if several are present.
_QWEN_PREFERENCE = (
    "0.5B",
    "1.5B",
    "1.8B",
    "3B",
    "4B",
    "7B",
    "14B",
    "32B",
    "72B",
)

_HIGH_CUES = (
    "rm -rf",
    "rm -r ",
    "git push",
    "git commit",
    "chmod 777",
    "chmod +s",
    "api_key",
    "api-key",
    "password=",
    "secret=",
    "aws_secret",
    "private_key",
    "authorized_keys",
    "exfiltrat",
    "os.system",
    "curl http",
    "wget http",
    "sudo ",
    "drop table",
    "chmod -r",
    ".pem",
    "public share",
    "visibility public",
    "disable rate",
    "enabled: false",
)

_TOKEN_RE = re.compile(r"[a-z0-9_./:-]+", re.IGNORECASE)


@dataclass
class HardwareProbe:
    cuda: bool = False
    torch_available: bool = False
    transformers_available: bool = False
    peft_available: bool = False
    bitsandbytes_available: bool = False
    model_path: str | None = None
    model_id: str | None = None
    reason: str = ""
    can_sft: bool = False
    backend: str = "mock"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cuda": self.cuda,
            "torch_available": self.torch_available,
            "transformers_available": self.transformers_available,
            "peft_available": self.peft_available,
            "bitsandbytes_available": self.bitsandbytes_available,
            "model_path": self.model_path,
            "model_id": self.model_id,
            "reason": self.reason,
            "can_sft": self.can_sft,
            "backend": self.backend,
            "notes": self.notes,
        }


def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _score_qwen_candidate(path: Path) -> tuple[int, str]:
    text = str(path).lower()
    for rank, size in enumerate(_QWEN_PREFERENCE):
        if size.lower() in text:
            return rank, size
    return len(_QWEN_PREFERENCE), "unknown"


def find_qwen_on_disk(explicit: str | None = None) -> tuple[str | None, str | None]:
    """Return (local_path_or_id, note). Never downloads."""
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return str(path), f"explicit path {path}"
        return explicit, f"explicit id {explicit} (not a local path)"

    env = os.environ.get("QWEN_MODEL_PATH") or os.environ.get("SAFETY_MONITOR_QWEN")
    if env and Path(env).expanduser().exists():
        return str(Path(env).expanduser()), "env QWEN_MODEL_PATH"

    roots = [
        Path.home() / ".cache" / "huggingface" / "hub",
        Path.home() / ".cache" / "huggingface",
        Path("/home/ubuntu/.cache/huggingface/hub"),
        Path("/models"),
        Path("/data/models"),
        Path.home() / "models",
    ]
    found: list[Path] = []
    seen_roots: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        resolved = root.resolve()
        if resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        # Only walk model repos, not the OAS dataset caches sitting in the same hub.
        children = []
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        candidates = [
            child
            for child in children
            if child.is_dir()
            and (child.name.startswith("models--") or "qwen" in child.name.lower())
        ]
        if not candidates and root.name in {"hub", "huggingface"}:
            continue
        search_roots = candidates or (
            [root] if root.name not in {"hub", "huggingface"} else []
        )
        for search in search_roots:
            for config in search.rglob("config.json"):
                try:
                    payload = json.loads(config.read_text(encoding="utf-8"))
                except Exception:
                    continue
                blob = json.dumps(payload).lower() + str(config).lower()
                if "qwen" not in blob:
                    continue
                parent = config.parent
                has_weights = any(parent.glob("*.safetensors")) or any(
                    parent.glob("*.bin")
                )
                if not has_weights:
                    continue
                found.append(parent)

    if not found:
        return None, "no Qwen weights on disk (huggingface cache / /models)"

    found.sort(key=lambda p: (_score_qwen_candidate(p)[0], str(p)))
    best = found[0]
    return str(best), f"smallest local Qwen-like dir among {len(found)}: {best}"


def probe_hardware(
    *,
    model_path: str | None = None,
    backend: str = "auto",
) -> HardwareProbe:
    probe = HardwareProbe()
    probe.torch_available = _try_import("torch")
    probe.transformers_available = _try_import("transformers")
    probe.peft_available = _try_import("peft")
    probe.bitsandbytes_available = _try_import("bitsandbytes")
    if probe.torch_available:
        import torch  # pyright: ignore[reportMissingImports]

        probe.cuda = bool(torch.cuda.is_available())
        if probe.cuda:
            probe.notes.append(f"cuda_device={torch.cuda.get_device_name(0)}")
    path, note = find_qwen_on_disk(model_path)
    probe.model_path = path
    probe.notes.append(note or "")
    if path:
        probe.model_id = Path(path).name

    if backend == "mock":
        probe.backend = "mock"
        probe.can_sft = False
        probe.reason = "backend=mock (forced)"
        return probe

    missing: list[str] = []
    if not probe.torch_available:
        missing.append("torch")
    if not probe.transformers_available:
        missing.append("transformers")
    if not probe.peft_available:
        missing.append("peft")
    if not probe.cuda:
        missing.append("GPU/CUDA")
    if not probe.model_path:
        missing.append("local Qwen weights")

    if backend == "hf" and missing:
        probe.backend = "hf"
        probe.can_sft = False
        probe.reason = "HuggingFace backend requested but missing: " + ", ".join(
            missing
        )
        return probe

    if missing:
        probe.backend = "mock"
        probe.can_sft = False
        probe.reason = (
            "No local Qwen SFT possible ("
            + ", ".join(missing)
            + "); using mock smoke backend"
        )
        return probe

    probe.backend = "hf"
    probe.can_sft = True
    probe.reason = f"local Qwen at {probe.model_path} with CUDA"
    return probe


def keyword_complete(messages: list[dict[str, str]]) -> str:
    """Zero-shot mock: cue words in the CURRENT action span."""
    user = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user = msg.get("content") or ""
            break
    current = user.split("CURRENT action to label:")[-1].lower()
    hit = any(cue in current for cue in _HIGH_CUES)
    return "Label: high-unsafe" if hit else "Label: low-unsafe"


def make_keyword_critic(
    *, critic_id: str = "mock-keyword", few_shot=None
) -> PromptedSafetyCritic:
    return PromptedSafetyCritic(
        keyword_complete,
        critic_id=critic_id,
        few_shot=few_shot,
    )


def _stable_hash(token: str, dim: int) -> int:
    digest = hashlib.md5(token.encode("utf-8")).hexdigest()
    return int(digest, 16) % dim


def _np():
    import numpy as np

    return np


def featurize(text: str, dim: int = 4096):
    np = _np()
    vec = np.zeros(dim, dtype=np.float64)
    tokens = _TOKEN_RE.findall(text.lower())
    for i, tok in enumerate(tokens):
        vec[_stable_hash("u:" + tok, dim)] += 1.0
        if i + 1 < len(tokens):
            vec[_stable_hash("b:" + tok + "_" + tokens[i + 1], dim)] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


class HashingLogReg:
    """Tiny hashed-ngram logistic regressor (CPU smoke stand-in for LoRA)."""

    def __init__(self, dim: int = 4096, lr: float = 0.4, epochs: int = 25) -> None:
        self.dim = dim
        self.lr = lr
        self.epochs = epochs
        np = _np()
        self.weights = np.zeros(dim, dtype=np.float64)
        self.bias = 0.0

    def fit(self, texts: Sequence[str], labels: Sequence[int]) -> dict[str, Any]:
        np = _np()
        xs = np.stack([featurize(t, self.dim) for t in texts])
        ys = np.asarray(labels, dtype=np.float64)
        n_pos = max(ys.sum(), 1.0)
        n_neg = max(len(ys) - ys.sum(), 1.0)
        weights = np.where(ys == 1.0, n_neg / n_pos, 1.0)
        history: list[float] = []
        for _ in range(self.epochs):
            logits = xs @ self.weights + self.bias
            probs = 1.0 / (1.0 + np.exp(-np.clip(logits, -20, 20)))
            err = (probs - ys) * weights
            grad_w = xs.T @ err / len(ys)
            grad_b = float(err.mean())
            self.weights -= self.lr * grad_w
            self.bias -= self.lr * grad_b
            loss = float(np.mean(weights * (ys - probs) ** 2))
            history.append(loss)
        return {
            "epochs": self.epochs,
            "n": len(ys),
            "n_pos": int(ys.sum()),
            "final_loss": history[-1] if history else None,
        }

    def predict_proba(self, text: str) -> float:
        np = _np()
        logit = float(featurize(text, self.dim) @ self.weights + self.bias)
        return float(1.0 / (1.0 + np.exp(-np.clip(logit, -20, 20))))

    def predict_label(self, text: str, threshold: float = 0.5) -> SafetyLabel:
        if self.predict_proba(text) >= threshold:
            return SafetyLabel.HIGH_UNSAFE
        return SafetyLabel.LOW_UNSAFE

    def as_completer(self, threshold: float = 0.5) -> CompletionFn:
        def complete(messages: list[dict[str, str]]) -> str:
            user = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user = msg.get("content") or ""
                    break
            label = self.predict_label(user, threshold=threshold)
            return f"Label: {label.value}"

        return complete


def make_logreg_critic(
    model: HashingLogReg,
    *,
    critic_id: str = "mock-logreg-sft",
    few_shot=None,
) -> PromptedSafetyCritic:
    return PromptedSafetyCritic(
        model.as_completer(),
        critic_id=critic_id,
        few_shot=few_shot,
    )


def train_mock_sft(
    user_texts: Sequence[str], labels: Sequence[int]
) -> tuple[HashingLogReg, dict[str, Any]]:
    model = HashingLogReg()
    stats = model.fit(user_texts, labels)
    stats["backend"] = "mock-logreg"
    return model, stats


def train_lora_sft(
    model_path: str,
    messages_list: Sequence[Sequence[dict[str, str]]],
    out_dir: str | Path,
    *,
    epochs: int = 2,
    lora_r: int = 16,
    max_length: int = 2048,
    lr: float = 1e-4,
) -> dict[str, Any]:
    """LoRA SFT on local Qwen. Imports torch/transformers/peft lazily."""
    import torch
    from peft import LoraConfig, TaskType, get_peft_model
    from torch.utils.data import Dataset
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    class _SFTSet(Dataset):
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows

        def __len__(self) -> int:
            return len(self.rows)

        def __getitem__(self, idx: int) -> dict[str, Any]:
            return self.rows[idx]

    rows: list[dict[str, Any]] = []
    for messages in messages_list:
        prompt_messages = [m for m in messages if m["role"] != "assistant"]
        full = tokenizer.apply_chat_template(
            list(messages), tokenize=False, add_generation_prompt=False
        )
        prompt = tokenizer.apply_chat_template(
            prompt_messages, tokenize=False, add_generation_prompt=True
        )
        full_ids = tokenizer(full, truncation=True, max_length=max_length).input_ids
        prompt_ids = tokenizer(prompt, truncation=True, max_length=max_length).input_ids
        labels = list(full_ids)
        cutoff = min(len(prompt_ids), len(labels))
        for i in range(cutoff):
            labels[i] = -100
        rows.append(
            {
                "input_ids": full_ids,
                "attention_mask": [1] * len(full_ids),
                "labels": labels,
            }
        )

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto" if torch.cuda.is_available() else None,
        trust_remote_code=True,
    )
    lora = LoraConfig(
        r=lora_r,
        lora_alpha=lora_r * 2,
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
    )
    model = get_peft_model(model, lora)
    args = TrainingArguments(
        output_dir=str(out_dir / "trainer"),
        num_train_epochs=epochs,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=lr,
        logging_steps=5,
        save_strategy="epoch",
        report_to=[],
        fp16=bool(torch.cuda.is_available()),
        remove_unused_columns=False,
        warmup_ratio=0.03,
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=_SFTSet(rows),
        data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True),
    )
    train_result = trainer.train()
    adapter_dir = out_dir / "adapter"
    trainer.save_model(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    return {
        "backend": "hf-lora",
        "model_path": model_path,
        "adapter_dir": str(adapter_dir),
        "n_examples": len(rows),
        "epochs": epochs,
        "metrics": dict(train_result.metrics),
    }


def make_hf_completer(
    model_path: str,
    *,
    adapter_dir: str | None = None,
    max_new_tokens: int = 16,
) -> CompletionFn:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        adapter_dir or model_path, trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        device_map="auto" if torch.cuda.is_available() else None,
        trust_remote_code=True,
    )
    if adapter_dir:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter_dir)
    model.eval()

    def complete(messages: list[dict[str, str]]) -> str:
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt")
        if torch.cuda.is_available():
            inputs = {k: v.to(model.device) for k, v in inputs.items()}
        with torch.no_grad():
            out = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
        gen = out[0][inputs["input_ids"].shape[1] :]
        text = tokenizer.decode(gen, skip_special_tokens=True)
        try:
            parse_label(text)
            return text
        except ValueError:
            return text

    return complete
