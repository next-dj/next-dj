from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from django.test import override_settings

from tests.support.access import COMPONENTS
from tests.support.helpers import ROUTER_OFF, file_router_config_entry
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
    """Route `roots` through one file router, the client router off unless asked."""
    first, *rest = roots
    entry = file_router_config_entry(pages_dir=first, dirs=list(rest))
    framework = {"PAGE_BACKENDS": [entry], "ROUTER": ROUTER_OFF, **framework}
    with override_settings(ROOT_URLCONF=urlconf, NEXT_FRAMEWORK=framework):
        yield


def templated_page(directory: Path, source: str, body: str | None = "<p>x</p>") -> Path:
    """Write a `page.py` of `source` in `directory` beside a `body` template."""
    return write_page(directory, source=source, body=body)


def layout_root(tmp_path: Path) -> Path:
    """Return a `pages` root in `tmp_path` with an `<html>` layout over every page."""
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text("<html>{% template %}</html>")
    return root


def framework(pages: Path) -> dict[str, object]:
    """Return a `NEXT_FRAMEWORK` routing `pages` with file components enabled."""
    return {
        "PAGE_BACKENDS": [file_router_config_entry(pages_dir=pages)],
        "COMPONENT_BACKENDS": COMPONENTS,
    }
