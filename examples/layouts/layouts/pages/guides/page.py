from next.deps import Depends
from next.pages import Context, context


@context("layout_theme_data")
def guides_layout_theme(
    layout_theme: dict[str, str] | None = Depends("layout_theme"),
) -> dict[str, str] | None:
    """Inject layout-level global dependency into this subpage."""
    return layout_theme


@context("parent_context_data")
def guides_parent_context(
    custom_variable: str | None = Context("custom_variable"),
) -> str | None:
    """Inject any context from parent/layout by name via Context("key")."""
    return custom_variable
