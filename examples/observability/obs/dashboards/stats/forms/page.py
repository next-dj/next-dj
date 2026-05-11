from obs import metrics

from next.pages import context


@context("dispatched")
def dispatched() -> list[tuple[str, int]]:
    """Return per-action dispatch counts in descending order."""
    return metrics.top_by_kind("forms.action_dispatched")


@context("validation_failed")
def validation_failed() -> list[tuple[str, int]]:
    """Return per-action validation failure counts."""
    return metrics.top_by_kind("forms.validation_failed")
