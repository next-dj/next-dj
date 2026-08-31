from next import context


@context("section")
def section() -> str:
    """Return the identifier used by the layout to mark the active doc section."""
    return "components"


@context("markup_sample")
def markup_sample() -> str:
    """Return the snippet the page sends through the child and the prop channel."""
    return "<em>emphasis</em>"
