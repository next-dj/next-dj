from next.pages import DContext, DGlobalContext, context


@context("layout_theme_data")
def guides_layout_theme(
    layout_theme: DGlobalContext["layout_theme"],  # noqa: F821
) -> dict[str, str] | None:
    """Inject layout-level global dependency into this subpage."""
    return layout_theme


@context("parent_context_data")
def guides_parent_context(
    custom_variable: DContext["custom_variable"],  # noqa: F821
) -> str | None:
    """Inject any context from parent/layout by name via DContext["key"]."""
    return custom_variable
