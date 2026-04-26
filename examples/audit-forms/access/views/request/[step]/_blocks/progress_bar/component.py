from typing import Any

from access.steps import STEP_FIELDS, STEP_LABEL, STEP_ORDER, normalise_step
from django.http import HttpRequest

from next.components import component


def _draft(request: HttpRequest) -> dict[str, Any]:
    return dict(request.session.get("access_request", {}))


@component.context("steps")
def _steps(request: HttpRequest, step: str = "applicant") -> list[dict[str, Any]]:
    """Synthesise step descriptors from the URL kwarg and the session draft.

    Status comes from session truth, not URL position: a step is
    ``"current"`` when it matches the URL kwarg, ``"saved"`` when every
    field it owns is persisted in ``request.session["access_request"]``,
    otherwise ``"pending"``. This keeps the bar honest if a user lands on
    step 2 without going through step 1.
    """
    current = normalise_step(step)
    draft = _draft(request)
    return [
        {
            "key": key,
            "label": STEP_LABEL[key],
            "index": index + 1,
            "status": _status(key, current, draft),
        }
        for index, key in enumerate(STEP_ORDER)
    ]


def _status(key: str, current: str, draft: dict[str, Any]) -> str:
    if key == current:
        return "current"
    fields = STEP_FIELDS[key]
    if fields and all(field in draft for field in fields):
        return "saved"
    return "pending"


@component.context("step_index")
def _step_index(step: str = "applicant") -> int:
    return STEP_ORDER.index(normalise_step(step)) + 1


@component.context("step_total")
def _step_total() -> int:
    return len(STEP_ORDER)


@component.context("step_label")
def _step_label(step: str = "applicant") -> str:
    return STEP_LABEL[normalise_step(step)]
