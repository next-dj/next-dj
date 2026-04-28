from typing import Final


STEP_ORDER: Final[list[str]] = ["applicant", "justification", "review"]
STEP_LABEL: Final[dict[str, str]] = {
    "applicant": "Applicant",
    "justification": "Justification",
    "review": "Review",
}
STEP_FIELDS: Final[dict[str, list[str]]] = {
    "applicant": ["full_name", "email", "team"],
    "justification": ["project_slug", "reason", "expires_in_days"],
    "review": [],
}


def normalise_step(value: str | None) -> str:
    """Return `value` if it is a known step, otherwise the first step."""
    if value not in STEP_ORDER:
        return STEP_ORDER[0]
    return value
