CALLS: list[str] = []
"""Appended to by the source bodies the tests write, to count their calls."""


def listed_elsewhere() -> list[dict[str, str]]:
    """List one post from a module that is no `sitemap.py`, for a sitemap to import."""
    return [{"slug": "elsewhere"}]
