from next.pages import context


@context("greeting")
def greeting() -> str:
    """Provide a context value the inline zone bodies read."""
    return "hi"


@context("seen", serialize=True)
def seen() -> int:
    """Provide a serialised value so the collector hydrates its JS context."""
    return 7
