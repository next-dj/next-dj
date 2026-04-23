from next.pages import context


@context("greeting")
def _greeting() -> str:
    return "Welcome to the next.dj template."
