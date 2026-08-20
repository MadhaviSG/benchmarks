"""Authored v6 task content, group A (catalog seeds 6100-6149).

Hand-written scenario definitions live in :mod:`v6_content_catalog`; this module
exports the first half as :data:`SEEDS`. Deterministic render via :mod:`v5_generate`.
"""

from __future__ import annotations

from safety_monitor.synthesis.v6_content_catalog import CATALOG
from safety_monitor.synthesis.v6_content_factory import build_all


SEEDS = build_all(CATALOG[:50])
