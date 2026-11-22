from __future__ import annotations

import pytest

from next.paths import PAGE_MARK
from next.router import CurrentLinks, current_links
from tests.support.markup import HOSTILE, flood


_HOSTILE_SIZE = 10_000
_LINK_COUNT = 50
_LINKS = "".join(
    f'<li><a class="item" href="/docs/section-{i % 5}/topic-{i}/">Topic {i}</a></li>'
    for i in range(_LINK_COUNT)
)
_ARTICLE = "".join(
    f'<p>Paragraph {i} with a <a href="/elsewhere/{i}/">link</a>.</p>'
    for i in range(20)
)
_DOCUMENT = (
    '<!DOCTYPE html><html lang="en"><head><title>Docs</title></head><body>'
    '<nav class="sidebar" data-sidebar><a href="/docs/">Docs</a>'
    f"<ul>{_LINKS}</ul></nav>"
    f"<main>{_ARTICLE}</main>"
    '<footer><p>No links to mark here, <a href="/about/">about</a>.</p></footer>'
    "</body></html>"
)
_PLAIN = f"<html><body><main>{_ARTICLE}</main></body></html>"
_PATH = "/docs/section-2/topic-7/?tab=api"
_ORIGIN = "https://example.com"


def _path() -> str:
    return _PATH


def _origin() -> str:
    return _ORIGIN


class TestBenchCurrentLinks:
    """The active-link pass over a rendered document, run once per response."""

    @pytest.mark.benchmark(group="router.current")
    def test_mark_fifty_links(self, benchmark) -> None:
        """A sidebar of fifty links, one current and its section root an ancestor."""
        links = current_links()
        assert links is not None
        marked = benchmark(links.mark, _DOCUMENT, _path, _origin)
        assert marked.count(PAGE_MARK) == 1
        assert marked.count('data-next-current="ancestor"') == 1

    @pytest.mark.benchmark(group="router.current")
    def test_mark_document_without_a_container(self, benchmark) -> None:
        """No navigation container, so the document comes back as it was passed."""
        links = current_links()
        assert links is not None
        assert benchmark(links.mark, _PLAIN, _path, _origin) is _PLAIN

    @pytest.mark.benchmark(group="router.current")
    def test_mark_with_a_custom_container(self, benchmark) -> None:
        """One attribute selector in place of the three default containers."""
        links = CurrentLinks(("[data-sidebar]",))
        marked = benchmark(links.mark, _DOCUMENT, _path, _origin)
        assert marked.count(PAGE_MARK) == 1

    @pytest.mark.benchmark(group="router.current")
    def test_current_links_lookup(self, benchmark) -> None:
        """The settings-named backend, held while the `ROUTER` mapping is the same."""
        assert benchmark(current_links) is current_links()


class TestBenchHostileMarkup:
    """Time hostile markup at two sizes, the ratio read off the bench report only.

    The walk is held linear by `TestLinearWork`, which counts steps, not time.
    """

    @pytest.mark.parametrize("size", [_HOSTILE_SIZE, 2 * _HOSTILE_SIZE])
    @pytest.mark.parametrize("unit", list(HOSTILE.values()), ids=list(HOSTILE))
    @pytest.mark.benchmark(group="router.current.hostile")
    def test_mark_hostile_markup(self, benchmark, unit: str, size: int) -> None:
        """One pass over a flood of the unit at the base size and at twice of it."""
        links = current_links()
        assert links is not None
        html = flood(unit, size)
        assert benchmark(links.mark, html, _path, _origin).count("<") == html.count("<")
