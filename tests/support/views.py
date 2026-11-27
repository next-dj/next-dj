from pathlib import Path


ZONED_BODY = 'a {% zone "z" %}<p>zoned</p>{% endzone %} b'
ZONE_HTML = '<div data-next-zone="z"><p>zoned</p></div>'


def write_dynamic_page(directory: Path, *, returns: str = "'<p>dynamic</p>'") -> Path:
    """Write a ``page.py`` whose ``render()`` returns `returns` on every request."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(f"def render(request):\n    return {returns}\n")
    return page_file
