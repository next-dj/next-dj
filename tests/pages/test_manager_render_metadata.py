from pathlib import Path

import pytest
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.test import RequestFactory, override_settings

from next.pages import page
from tests.support import unified_view


_HEADED = Path(__file__).resolve().parent.parent / "site_pages" / "headed" / "page.py"


def _render(query: str = "") -> str:
    response = unified_view(page, _HEADED)(RequestFactory().get(f"/headed/{query}"))
    assert isinstance(response, HttpResponse | TemplateResponse)
    assert response.status_code == 200
    return response.content.decode()


class TestUnifiedViewRendersTheHead:
    """A layout's ``{% metadata %}`` renders the folded chain of the page it wraps."""

    def test_the_head_carries_the_page_metadata(self) -> None:
        html = _render()
        assert html.startswith("<html><head><title>Headed</title>\n")
        assert "<h1>headed page</h1>" in html

    def test_the_description_is_escaped(self) -> None:
        html = _render()
        assert "<script>alert(1)</script>" not in html
        assert 'content="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">' in html

    def test_jsonld_never_closes_the_script(self) -> None:
        html = _render()
        assert "</script><!--" not in html
        assert (
            '<script type="application/ld+json">{"@type": "WebPage", '
            '"name": "\\u003C/script\\u003E\\u003C!--"}</script>' in html
        )

    def test_the_canonical_is_the_self_url_under_the_base(self) -> None:
        assert '<link rel="canonical" href="https://acme.example/headed/">' in (
            _render("?utm=1&page=1")
        )

    @override_settings(NEXT_FRAMEWORK={"METADATA": {"CANONICAL_QUERY": ("page",)}})
    def test_the_canonical_keeps_the_allowlisted_query(self) -> None:
        html = _render("?utm=1&page=2")
        assert '<link rel="canonical" href="https://acme.example/headed/?page=2">' in (
            html
        )

    def test_og_is_derived_from_the_page(self) -> None:
        html = _render()
        assert '<meta property="og:title" content="Headed">' in html
        assert '<meta property="og:url" content="https://acme.example/headed/">' in html
        assert '<meta property="og:type" content="website">' in html

    @pytest.mark.parametrize("query", ["", "?page=2"], ids=["bare", "paged"])
    def test_every_render_resolves_afresh(self, query: str) -> None:
        assert "<title>Headed</title>" in _render(query)
