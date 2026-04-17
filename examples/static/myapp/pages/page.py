from next.pages import context


styles = [
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap",
]
scripts: list[str] = []


@context("page_title")
def get_page_title() -> str:
    """Static greeting used by the home page template."""
    return "Home"
