"""Tests for --disable-security-policy functionality."""

from pathlib import Path

import pytest

from benchmarks.utils.args_parser import get_parser
from benchmarks.utils.models import EvalMetadata
from openhands.sdk.critic import PassCritic


@pytest.fixture
def base_metadata_kwargs():
    """Common kwargs for EvalMetadata construction."""
    return {
        "llm": {
            "model": "test-model",
            "api_key": "test-key",
        },
        "dataset": "test/dataset",
        "dataset_split": "test",
        "max_iterations": 100,
        "eval_output_dir": "/tmp/test",
        "critic": PassCritic(),
    }


def test_disable_security_policy_argument_default():
    """Test --disable-security-policy defaults to False."""
    parser = get_parser(add_llm_config=True)
    args = parser.parse_args(["config.json", "--dataset", "test", "--split", "train"])
    assert args.disable_security_policy is False


def test_disable_security_policy_argument_enabled():
    """Test --disable-security-policy flag sets True."""
    parser = get_parser(add_llm_config=True)
    args = parser.parse_args(
        [
            "config.json",
            "--dataset",
            "test",
            "--split",
            "train",
            "--disable-security-policy",
        ]
    )
    assert args.disable_security_policy is True


def test_eval_metadata_disable_security_policy_default(base_metadata_kwargs):
    """Test EvalMetadata defaults disable_security_policy to False."""
    metadata = EvalMetadata(**base_metadata_kwargs)
    assert metadata.disable_security_policy is False


def test_eval_metadata_disable_security_policy_enabled(base_metadata_kwargs):
    """Test EvalMetadata accepts disable_security_policy=True."""
    metadata = EvalMetadata(**base_metadata_kwargs, disable_security_policy=True)
    assert metadata.disable_security_policy is True


def test_no_security_policy_template_exists():
    """Test that no_security_policy.j2 template exists."""
    template_path = (
        Path(__file__).parent.parent
        / "benchmarks"
        / "openagentsafety"
        / "prompts"
        / "no_security_policy.j2"
    )
    assert template_path.exists(), (
        f"no_security_policy.j2 template not found at {template_path}"
    )


def test_no_security_policy_template_is_empty():
    """Test that no_security_policy.j2 renders to empty/minimal content."""
    template_path = (
        Path(__file__).parent.parent
        / "benchmarks"
        / "openagentsafety"
        / "prompts"
        / "no_security_policy.j2"
    )
    content = template_path.read_text().strip()
    # Should only contain a Jinja2 comment, rendering to empty string
    assert content.startswith("{#") and content.endswith("#}")
