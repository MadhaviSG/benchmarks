"""Online monitor evaluation: credence aggregation and lead-time metrics.

The SFT experiment asks "can a critic label this action?". This package asks
the deployment question instead: replaying a recorded trajectory one step at a
time, how much credence does a monitor accumulate, and does it cross a decision
threshold *before* the harmful action lands?
"""

from __future__ import annotations
