from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from django.test import override_settings

from tests.support.helpers import file_router_config_entry
from tests.support.pages import write_page
from tests.support.sites import NAMESPACED_URLCONF


if TYPE_CHECKING:
    from collections.abc import Generator, Iterable
    from pathlib import Path


METADATA_LAYOUT = "<html><head>{% metadata %}</head><body>{% template %}</body></html>"
NOINDEX = 'template = "x"\nmetadata = {"robots": {"index": False}}\n'
POSTS_ITEMS = """
from next.seo import sitemap


@sitemap.items("posts/[slug]")
def posts():
    yield {"slug": "a"}
"""


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
    for name, text in (("sitemap.py", sitemap), ("robots.py", robots)):
        if text is not None:
            (root / name).write_text(text)
    if robots_txt is not None:
        (root / "robots.txt").write_bytes(robots_txt)
    return root


def write_metadata_tree(root: Path, *pages: tuple[str, str], sitemap: str = "") -> Path:
    """Write a sitemap tree under the metadata layout, each page with its literal."""
    write_tree(root, pages=(), sitemap=sitemap)
    (root / "layout.djx").write_text(METADATA_LAYOUT)
    for trail, metadata in pages:
        write_page(root, trail, f"metadata = {metadata}\n", body="<p>x</p>")
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
