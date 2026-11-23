from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from next.forms.origin import resolve_url_to_page
from next.partial.layers import LAYER_ATTR, stash_layers
from next.static import StaticCollector
from next.static.runtime import BUNDLE_NOTE
from next.testing import NextClient, envelope_of
from tests.support import routed, write_page
from tests.support.access import PAGE_HEAD


pytestmark = pytest.mark.django_db

_SHEET = '{% zone "sheet" %}<form>sheet {{ pk }}</form>{% endzone %}'
_SKELETON = '<p class="bone">{{ request.path }}</p>'
_STASH = '<template data-next-layer="sheet"><p class="bone">/home/</p></template>'
_LINK = '<a href="/sheet/1/" data-next-layer="sheet">open</a>'
_RELOAD = "/static/next/next.reload.min.js"


def _site(root: Path, home: str = _LINK, *, sheet: str = PAGE_HEAD) -> None:
    write_page(root, "home", PAGE_HEAD, body=home)
    write_page(root, "sheet/[int:pk]", sheet, body=_SHEET)
    (root / "sheet" / "[int:pk]" / "loading.sheet.djx").write_text(_SKELETON)


def _stashed(
    root: Path, html: str, path: str = "/home/"
) -> tuple[str, StaticCollector]:
    request = RequestFactory().get(path)
    request.user = AnonymousUser()
    collector = StaticCollector()
    with routed(root):
        return stash_layers(html, collector, request), collector


class TestStash:
    """A page carries the loading UI of each layer zone before the first link to it."""

    def test_the_first_link_of_a_zone_gets_the_loading_ui_once(
        self, root: Path
    ) -> None:
        _site(root)
        links = _LINK + '<a href="/sheet/2/" data-next-layer="sheet">two</a>'
        html, _collector = _stashed(root, f"<ul><li>{links}</li></ul>")
        assert html == f"<ul><li>{_STASH}{links}</li></ul>"

    def test_a_page_without_a_layer_link_is_left_alone(self, root: Path) -> None:
        _site(root)
        assert _stashed(root, "<p>no layer</p>")[0] == "<p>no layer</p>"

    def test_a_render_without_a_request_is_left_alone(self) -> None:
        assert stash_layers(_LINK, StaticCollector(), None) == _LINK

    @pytest.mark.parametrize(
        "link",
        [
            pytest.param(
                "<a href='/sheet/1/' data-next-layer='sheet'>o</a>", id="single"
            ),
            pytest.param("<a data-next-layer=sheet href=/sheet/1/>o</a>", id="bare"),
            pytest.param(
                '<a\n  title="a > b"\n  data-next-layer = "sheet"  href="/sheet/1/">o</a>',
                id="spaced",
            ),
            pytest.param(
                '<a href="/sheet/1/?a=1&amp;b=2" data-next-layer="sheet">o</a>',
                id="entity",
            ),
            pytest.param(
                '<a href="../sheet/1/" data-next-layer="sheet">o</a>', id="relative"
            ),
            pytest.param(
                '<a href="http://testserver/sheet/1/" data-next-layer="sheet">o</a>',
                id="absolute",
            ),
            pytest.param(
                '<a href="/sheet/1/" href="/home/" data-next-layer="sheet">o</a>',
                id="repeated",
            ),
        ],
    )
    def test_any_spelling_of_the_link_is_read(self, root: Path, link: str) -> None:
        _site(root)
        html, _collector = _stashed(root, link)
        assert html == _STASH + link

    @pytest.mark.parametrize(
        "html",
        [
            pytest.param(f"<!-- {_LINK} -->", id="comment"),
            pytest.param(f"<script>const a = '{_LINK}';</script>", id="script"),
            pytest.param(f"<template>{_LINK}</template>", id="template"),
            pytest.param(f"<textarea>{_LINK}</textarea>", id="textarea"),
            pytest.param('<a href="/sheet/1/" data-next-layer="">o</a>', id="no-zone"),
            pytest.param('<a data-next-layer="sheet">o</a>', id="no-href"),
            pytest.param(
                '<a href="/sheet/1/" data-next-layered="sheet">o</a>', id="other"
            ),
            pytest.param(
                '<a href="https://elsewhere.test/sheet/1/" data-next-layer="sheet">o</a>',
                id="foreign",
            ),
            pytest.param(
                '<a href="/nowhere/" data-next-layer="sheet">o</a>', id="unrouted"
            ),
            pytest.param(
                '<a href="/home/" data-next-layer="sheet">o</a>', id="no-file"
            ),
        ],
    )
    def test_a_link_no_layer_loading_answers_gets_none(
        self, root: Path, html: str
    ) -> None:
        _site(root)
        stashed, collector = _stashed(root, html)
        assert stashed == html
        assert not collector.notes(BUNDLE_NOTE)

    def test_the_first_link_of_a_zone_decides_for_the_page(self, root: Path) -> None:
        _site(root)
        html = '<a href="/home/" data-next-layer="sheet">o</a>' + _LINK
        assert _stashed(root, html)[0] == html

    def test_a_stash_needs_the_reload_bundle(self, root: Path) -> None:
        _site(root)
        collector = StaticCollector(bundle_url=lambda name: f"/static/{name}.js")
        request = RequestFactory().get("/home/")
        with routed(root):
            stash_layers(_LINK, collector, request)
        assert collector.notes(BUNDLE_NOTE) == ["/static/reload.js"]

    def test_the_intercept_serving_the_zone_names_the_file(self, root: Path) -> None:
        _site(root)
        write_page(root, "photo/[int:pk]", PAGE_HEAD, body="<p>photo</p>")
        write_page(root, "(.)photo/[int:pk]", PAGE_HEAD, body=_SHEET)
        own = root / "(.)photo" / "[int:pk]" / "loading.sheet.djx"
        own.write_text("<i>peek</i>")
        link = '<a href="/photo/1/" data-next-layer="sheet">o</a>'
        html, _collector = _stashed(root, link)
        assert html == f'<template {LAYER_ATTR}="sheet"><i>peek</i></template>{link}'

    def test_the_page_behind_a_path_is_resolved_once(self, root: Path) -> None:
        _site(root)
        request = RequestFactory().get("/home/")
        with (
            routed(root),
            mock.patch(
                "next.partial.layers.resolve_url_to_page", wraps=resolve_url_to_page
            ) as resolving,
        ):
            first = stash_layers(_LINK, StaticCollector(), request)
            again = stash_layers(_LINK, StaticCollector(), request)
        assert first == again == _STASH + _LINK
        assert resolving.call_count == 1


class TestResponses:
    """Every page and zone answer carries the loading UI its layer links need."""

    def test_a_document_carries_it_before_the_link(self, root: Path) -> None:
        _site(root)
        with routed(root):
            html = NextClient().get("/home/").content.decode()
        assert _STASH + _LINK in html

    def test_a_zone_answer_carries_it_with_the_reload_bundle(self, root: Path) -> None:
        _site(root, '{% zone "list" %}' + _LINK + "{% endzone %}")
        with routed(root):
            response = NextClient().get_zones("/home/", "list")
        envelope = envelope_of(response)
        [morph] = [op for op in envelope.ops if op["op"] == "morph"]
        assert _STASH + _LINK in morph["html"]
        assert _RELOAD in [asset["url"] for asset in envelope.assets]

    def test_an_appended_page_of_rows_carries_it_too(self, root: Path) -> None:
        _site(root, '{% zone "list" %}' + _LINK + "{% endzone %}")
        with routed(root):
            response = NextClient().get_zones(
                "/home/", "list", headers={"X-Next-Merge": "append"}
            )
        [append] = [op for op in envelope_of(response).ops if op["op"] == "append"]
        assert append["html"] == _STASH + _LINK
