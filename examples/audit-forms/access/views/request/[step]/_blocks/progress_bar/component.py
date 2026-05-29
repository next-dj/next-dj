from typing import Any

from next.components import component
from next.forms import FormWizard


_STEP_LABELS = {
    "identity": "Identity",
    "scope": "Scope",
    "approval": "Approval",
}


def _label(step: str) -> str:
    return _STEP_LABELS.get(step, step.replace("_", " ").title())


@component.context("steps")
def steps(wizard: FormWizard) -> list[dict[str, Any]]:
    """Describe each step with its label, index, and status from the wizard.

    Status is sourced from wizard storage truth, not URL position: a step
    is ``"current"`` when it is the active step, ``"saved"`` when it has
    stored data, otherwise ``"pending"``.
    """
    current = wizard.current_step()
    completed = set(wizard.completed_steps())
    return [
        {
            "key": name,
            "label": _label(name),
            "index": index + 1,
            "status": _status(name, current, completed),
        }
        for index, name in enumerate(wizard.step_names())
    ]


def _status(key: str, current: str, completed: set[str]) -> str:
    if key == current:
        return "current"
    if key in completed:
        return "saved"
    return "pending"


@component.context("step_index")
def step_index(wizard: FormWizard) -> int:
    names = wizard.step_names()
    return names.index(wizard.current_step()) + 1


@component.context("step_total")
def step_total(wizard: FormWizard) -> int:
    return len(wizard.step_names())


@component.context("step_label")
def step_label(wizard: FormWizard) -> str:
    return _label(wizard.current_step())
