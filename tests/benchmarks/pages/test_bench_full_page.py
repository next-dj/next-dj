"""One full page through the test client, against the same HTML from a Django view.

The page carries a head of about twenty tags, a `scripts.py` with consent-gated
scripts, and a configured consent banner, so the row prices every per-request layer
the framework adds to a page view: the response policy, the metadata fold and head
render, the scripts plan, the consent payload and the static injection.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import pytest
from django.http import HttpRequest, HttpResponse
from django.template import engines
from django.test import Client, override_settings
from django.urls import path

from tests.support import BASE, routed


if TYPE_CHECKING:
    from pathlib import Path


_ROWS = "".join(f"<p>row {i}</p>" for i in range(10))
_LAYOUT = (
    "<html><head>{% metadata %}{% collect_styles %}</head>"
    "<body>{% template %}{% collect_scripts %}</body></html>"
)
_PAGE = f"""
template = "<main><h1>Wallet</h1>{_ROWS}</main>"
metadata = {{
    "title": "Wallet",
    "description": "The wallet of the account.",
    "canonical": True,
    "keywords": ["wallet", "money"],
    "robots": {{"index": True, "follow": True, "googlebot": {{"nosnippet": True}}}},
    "og": {{"type": "website", "images": ["/og.png"]}},
    "twitter": {{"card": "summary", "site": "@acme"}},
    "theme_color": "#111111",
    "alternates": {{"feeds": [{{"url": "/feed.xml", "type": "rss"}}]}},
    "jsonld": [{{"@type": "WebPage"}}],
}}
"""
_SCRIPTS = """
from next.scripts import Script, Strategy

scripts = (
    Script("consent", init="window.dataLayer=[]", strategy=Strategy.BLOCKING),
    Script("base", src="https://cdn.example/base.js"),
    Script("gtm", src="https://gtm.example/gtm.js", category="analytics"),
    Script("pixel", src="https://px.example/p.js", init="px()", category="marketing"),
    Script("chat", src="https://chat.example/c.js", strategy=Strategy.IDLE),
)
"""
_FRAMEWORK = {
    "SITE": {"URL": BASE, "NAME": "Acme"},
    "CONSENT": {"CATEGORIES": ["necessary", "analytics", "marketing"]},
}


def _write_site(root: Path) -> None:
    """Write the page tree the framework row serves."""
    root.mkdir(parents=True)
    (root / "layout.djx").write_text(_LAYOUT)
    (root / "page.py").write_text(_PAGE)
    (root / "scripts.py").write_text(_SCRIPTS)


def _django_urlconf(html: str) -> types.ModuleType:
    """Return a URLconf whose one view renders `html` as a Django template."""
    template = engines["django"].from_string(html)

    def view(request: HttpRequest) -> HttpResponse:
        return HttpResponse(template.render({}, request))

    module = types.ModuleType("bench_full_page_urls")
    module.urlpatterns = [path("", view)]
    return module


class TestBenchFullPage:
    """A GET of one full page, warm, through every middleware the test site lists."""

    @pytest.mark.benchmark(group="pages.full")
    def test_framework_page(self, tmp_path: Path, benchmark) -> None:
        root = tmp_path / "pages"
        _write_site(root)
        client = Client()
        with routed(root, **_FRAMEWORK):
            response = client.get("/")
            assert response.status_code == 200
            benchmark(client.get, "/")

    @pytest.mark.benchmark(group="pages.full")
    def test_django_view_serving_the_same_html(self, tmp_path: Path, benchmark) -> None:
        """The HTML the framework row sends, rendered by a plain Django view."""
        root = tmp_path / "pages"
        _write_site(root)
        client = Client()
        with routed(root, **_FRAMEWORK):
            html = client.get("/").content.decode()
        with override_settings(ROOT_URLCONF=_django_urlconf(html)):
            assert client.get("/").status_code == 200
            benchmark(client.get, "/")
