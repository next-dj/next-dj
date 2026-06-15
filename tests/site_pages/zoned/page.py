from next.pages import context


@context("greeting")
def greeting() -> str:
    """Provide a context value the zone bodies read."""
    return "hi"


@context("flag", serialize=True)
def flag() -> bool:
    """Provide a serialised value so the collector hydrates its JS context."""
    return True
