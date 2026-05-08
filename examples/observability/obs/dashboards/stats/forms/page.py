from obs import metrics

from next.pages import context


@context("dispatched")
def dispatched() -> list[tuple[str, int]]:
    """Return per-action dispatch counts in descending order."""
    rendered = metrics.read_kind("forms.action_dispatched")
    return sorted(rendered.items(), key=lambda item: -item[1])


@context("validation_failed")
def validation_failed() -> list[tuple[str, int]]:
    """Return per-action validation failure counts."""
    failed = metrics.read_kind("forms.validation_failed")
    return sorted(failed.items(), key=lambda item: -item[1])
