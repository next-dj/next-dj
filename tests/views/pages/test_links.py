from collections.abc import Callable, Iterator
from pathlib import Path
from unittest import mock

import pytest
from django.http import HttpResponse

from next.router.links import StaticLinks, TemplateLinks
from next.testing import envelope_of, override_next_settings
from tests.support import (
    build_nested_page,
    build_page_request,
    build_zone_request,
    unified_view,
    write_page,
)


_NAV = '<nav><a href="/">Home</a><a href="/docs/">Docs</a></nav>'
_HOME = '<a href="/" aria-current="page" data-next-current="page">Home</a>'
_DOCS = '<a href="/docs/" aria-current="page" data-next-current="page">Docs</a>'
_STATIC = f"template = {_NAV!r}"
_AWAITED = f"async def render(request):\n    return {_NAV!r}\n"
_PASS_OFF = {"ENABLED": False, "ACTIVE_LINKS": {"ENABLED": False}}


class TestActiveLinks:
    """The active-link pass marks every document and zone the view renders, once."""

    @pytest.fixture()
    def counted(self) -> Iterator[Callable[[], int]]:
        """Count the passes over a document and over its fixed containers alike."""
        with (
            mock.patch.object(
                TemplateLinks, "mark", autospec=True, side_effect=TemplateLinks.mark
            ) as document,
            mock.patch.object(
                StaticLinks, "mark", autospec=True, side_effect=StaticLinks.mark
            ) as fixed,
        ):
            yield lambda: document.call_count + fixed.call_count

    @pytest.fixture()
    def document(self, page_instance, tmp_path: Path) -> Callable[[str], HttpResponse]:
        """Return a renderer of the page whose body source is the argument."""

        def render(source: str) -> HttpResponse:
            page_file = write_page(tmp_path, source=source)
            response = unified_view(page_instance, page_file)(build_page_request())
            assert isinstance(response, HttpResponse)
            return response

        return render

    def test_a_document_is_marked_with_the_router_off(self, document, counted) -> None:
        response = document(_STATIC)
        assert response.content.decode() == _NAV.replace('<a href="/">Home</a>', _HOME)
        assert counted() == 1

    def test_a_routed_document_is_marked_once(self, document, counted) -> None:
        with override_next_settings(ROUTER={"ENABLED": True}):
            response = document(_STATIC)
        assert _HOME in response.content.decode()
        assert counted() == 1

    @pytest.mark.parametrize("source", [_STATIC, _AWAITED], ids=["static", "awaited"])
    def test_the_pass_off_leaves_the_bytes(
        self, document, counted, source: str
    ) -> None:
        with override_next_settings(ROUTER=_PASS_OFF):
            response = document(source)
        assert response.content.decode() == _NAV
        assert counted() == 0

    @pytest.mark.parametrize(
        ("origin", "marked"),
        [
            pytest.param(None, _HOME, id="zone-marks-its-page"),
            pytest.param("/docs/", _DOCS, id="layer-marks-the-page-beneath"),
        ],
    )
    def test_zone_html_is_marked_for_the_base_page(
        self, page_instance, tmp_path, origin: str | None, marked: str
    ) -> None:
        page_file = build_nested_page(
            tmp_path, body=f'{{% zone "z" %}}{_NAV}{{% endzone %}}'
        )
        request = build_zone_request("z")
        if origin is not None:
            request.META["HTTP_X_NEXT_ORIGIN"] = origin
        response = unified_view(page_instance, page_file)(request)
        assert marked in envelope_of(response).html_for_zone("z")

    def test_zone_html_is_untouched_with_the_pass_off(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(
            tmp_path, body=f'{{% zone "z" %}}{_NAV}{{% endzone %}}'
        )
        with override_next_settings(ROUTER=_PASS_OFF):
            response = unified_view(page_instance, page_file)(build_zone_request("z"))
        assert envelope_of(response).html_for_zone("z") == (
            f'<div data-next-zone="z">{_NAV}</div>'
        )
