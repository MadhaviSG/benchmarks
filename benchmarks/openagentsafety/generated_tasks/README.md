# Generated OpenAgentSafety task variants

This directory contains three surface-diverse variants for each of the 15 seed
tasks in `oas-seeds1_fa5f.zip` (45 tasks total). Each task is self-contained and
contains `task.md`, `checkpoints.md`, `safe_completion.md`, and mock workspace
files. NPC tasks also contain `scenarios.json`.

The seed mix is preserved exactly by expanding every seed equally: 21 NPC tasks
and 24 non-NPC tasks (a 7:8 ratio), including 6 harness-only tasks. The
variants retain the seed taxonomy and unsafe motif while changing the setting,
people, file names, and operational details.

`manifest.json` records the seed mapping, risk category, NPC/harness flags, and
workspace file inventory for reproducible dataset assembly. Browser/service
variants marked `harness_only` require the live OpenHands service harness; the
other variants can be staged from their local workspace files.
