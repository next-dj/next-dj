from next.pages import context


@context("greeting")
def greeting() -> str:
    return "Welcome to the next.dj template."
