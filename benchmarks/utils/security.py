"""
Security analyzer utilities for benchmarks.

This module provides factory functions for creating security analyzers
that can be used in benchmark evaluations.
"""

import json
from argparse import ArgumentParser, Namespace
from pathlib import Path

from openhands.sdk import get_logger
from openhands.sdk.security import GraySwanAnalyzer, LLMSecurityAnalyzer, SecurityAnalyzerBase


logger = get_logger(__name__)


SECURITY_ANALYZER_CHOICES = ["none", "grayswan", "llm"]

SECURITY_ANALYZER_NAME_TO_CLASS: dict[str, type[SecurityAnalyzerBase]] = {
    "grayswan": GraySwanAnalyzer,
    "llm": LLMSecurityAnalyzer,
}


def add_security_analyzer_args(parser: ArgumentParser) -> None:
    """Add security analyzer arguments to an argparse parser."""
    parser.add_argument(
        "--security-analyzer",
        type=str,
        default="none",
        choices=SECURITY_ANALYZER_CHOICES,
        help=(
            "Security analyzer to use during inference (default: none). "
            "Options: "
            "'none' - No security analysis, "
            "'grayswan' - GraySwan Cygnal API (requires GRAYSWAN_API_KEY env var), "
            "'llm' - LLM-based security analyzer."
        ),
    )
    parser.add_argument(
        "--security-analyzer-config",
        type=str,
        default=None,
        help=(
            "Path to JSON config file with security analyzer parameters "
            "(e.g., {'timeout': 60, 'policy_id': 'xyz'} for grayswan)"
        ),
    )


def create_security_analyzer(args: Namespace) -> SecurityAnalyzerBase | None:
    """Create a security analyzer from parsed argparse arguments.

    Args:
        args: Parsed arguments from argparse

    Returns:
        SecurityAnalyzerBase instance, or None if no analyzer is configured

    Example:
        parser = get_parser()
        args = parser.parse_args(['--security-analyzer', 'grayswan'])
        analyzer = create_security_analyzer(args)
    """
    analyzer_name = getattr(args, "security_analyzer", "none")

    if analyzer_name == "none" or analyzer_name is None:
        return None

    # Load config if provided
    kwargs: dict = {}
    config_path_str = getattr(args, "security_analyzer_config", None)
    if config_path_str:
        config_path = Path(config_path_str)
        if not config_path.exists():
            raise ValueError(
                f"Security analyzer config file not found: {config_path_str}"
            )
        with open(config_path) as f:
            kwargs = json.load(f)
        logger.info(
            f"Loaded security analyzer config from {config_path_str}: "
            f"{list(kwargs.keys())}"
        )

    if analyzer_name not in SECURITY_ANALYZER_NAME_TO_CLASS:
        raise ValueError(
            f"Unknown security analyzer: {analyzer_name}. "
            f"Available: {', '.join(SECURITY_ANALYZER_CHOICES)}"
        )

    analyzer_class = SECURITY_ANALYZER_NAME_TO_CLASS[analyzer_name]
    analyzer = analyzer_class(**kwargs)
    logger.info(f"Created security analyzer: {analyzer_name} with args: {kwargs}")
    return analyzer
