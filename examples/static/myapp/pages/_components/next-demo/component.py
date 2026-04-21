from next.components import context


@context("theme", serialize=True)
def get_theme() -> str:
    return "dark"
