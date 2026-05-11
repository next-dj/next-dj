from obs.forms import WINDOW_CHOICES

from next.components import component


@component.context("window_options")
def window_options() -> list[tuple[str, str]]:
    """Expose the choice list to the template for label rendering."""
    return list(WINDOW_CHOICES)
