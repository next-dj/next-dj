from __future__ import annotations

from flags.metrics import render_counts
from flags.receivers import feature_guard_count

from next.pages import context


context("render_counts")(render_counts)
context("feature_guard_count")(feature_guard_count)
