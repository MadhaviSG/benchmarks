"""Authored v6 task content, group D (catalog seeds 6330-6439)."""

from __future__ import annotations

from safety_monitor.synthesis.v6_catalog_ext import CATALOG_EXT
from safety_monitor.synthesis.v6_content_factory import build_all


SEEDS = build_all(CATALOG_EXT[110:220])
