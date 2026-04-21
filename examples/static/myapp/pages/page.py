from next.pages import context


styles = [
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap",
]
scripts: list[str] = []


@context("page_title")
def get_page_title() -> str:
    return "Home"


@context("page_meta", serialize=True)
def get_page_meta() -> dict:
    return {"page": "home", "version": "0.4"}


@context("theme", inherit_context=True, serialize=True)
def get_theme() -> str:
    return "dark"
