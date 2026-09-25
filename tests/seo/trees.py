from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from django.test import override_settings

from tests.support import file_router_config_entry, write_page


if TYPE_CHECKING:
    from collections.abc import Generator, Iterable
    from pathlib import Path


BASE = "https://acme.example"
NAMESPACED_URLCONF = "tests.urls.urls_namespaced"
WITH_BASE = {"METADATA": {"DEFAULTS": {"base": BASE}}}
PREFIXED_URLCONF = "tests.seo.urls_prefixed"
USER_URLCONF = "tests.seo.urls_user"
NOINDEX = 'template = "x"\nmetadata = {"robots": {"index": False}}\n'
POSTS_ITEMS = """
from next.seo import sitemap


@sitemap.items("posts/[slug]")
def posts():
    yield {"slug": "a"}
"""
CALLS: list[str] = []
"""Appended to by the `sitemap.py` bodies the tests write, to count their calls."""


def write_tree(
    root: Path,
    *,
    pages: Iterable[str] = ("",),
    sitemap: str | None = None,
    robots: str | None = None,
    robots_txt: bytes | None = None,
) -> Path:
    """Write a page tree with the SEO sources named and return its root."""
    root.mkdir(parents=True, exist_ok=True)
    for trail in pages:
        write_page(root, trail)
    if sitemap is not None:
        (root / "sitemap.py").write_text(sitemap)
    if robots is not None:
        (root / "robots.py").write_text(robots)
    if robots_txt is not None:
        (root / "robots.txt").write_bytes(robots_txt)
    return root


@contextmanager
def routed(
    *roots: Path, urlconf: str = NAMESPACED_URLCONF, **framework: object
) -> Generator[None, None, None]:
    """Route `roots` through one file router mounted by `urlconf` for the block."""
    first, *rest = roots
    entry = file_router_config_entry(pages_dir=first, dirs=list(rest))
    with override_settings(
        ROOT_URLCONF=urlconf, NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry], **framework}
    ):
        yield


def listed_elsewhere() -> list[dict[str, str]]:
    """List one post from a module that is no `sitemap.py`, for a sitemap to import."""
    return [{"slug": "elsewhere"}]
