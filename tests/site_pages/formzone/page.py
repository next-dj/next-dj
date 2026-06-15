from next.pages import context


@context("greeting")
def greeting() -> str:
    """Provide a default the form override sits beside."""
    return "hi"
