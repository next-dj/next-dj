import markdown


def render_markdown(text: str) -> str:
    """Convert a Markdown document body to HTML."""
    return markdown.markdown(text, extensions=["fenced_code"])
