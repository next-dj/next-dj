from __future__ import annotations

from typing import Any

from next.components import component


@component.context("step_label")
def _step_label(progress_steps: list[dict[str, Any]]) -> str:
    """Return the label of the step currently marked `current`."""
    for entry in progress_steps:
        if entry["status"] == "current":
            return str(entry["label"])
    return ""


@component.context("completed_count")
def _completed_count(progress_steps: list[dict[str, Any]]) -> int:
    return sum(1 for entry in progress_steps if entry["status"] == "done")
