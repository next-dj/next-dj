from pathlib import Path

import pytest

from next.partial import (
    ForeignPageNotAuthorizedError,
    OriginSource,
    Patches,
    resolve_partial_origin,
)
from tests.support import partial_request


_PAGES_ROOT = Path(__file__).resolve().parent.parent / "site_pages"
_ZONED_PAGE = _PAGES_ROOT / "zoned" / "page.py"
_REDIRECTING_PAGE = _PAGES_ROOT / "redirecting" / "page.py"


class TestForeignZoneByPath:
    """`morph(zone=, page=)` renders the zone of a page named by its path."""

    def test_foreign_page_path_morphs_its_zone(self) -> None:
        envelope = (
            Patches(partial_request()).morph(zone="alpha", page=_ZONED_PAGE).envelope()
        )
        op = envelope.ops[0].as_dict()
        assert op["op"] == "morph"
        assert op["target"] == {"zone": "alpha"}
        assert op["html"] == '<div data-next-zone="alpha"><p>alpha hi</p></div>'

    def test_foreign_zone_assets_travel_in_the_envelope(self) -> None:
        envelope = (
            Patches(partial_request()).morph(zone="alpha", page=_ZONED_PAGE).envelope()
        )
        assert {"kind": "css", "url": "/static/next/zoned.css"} in [
            asset.as_dict() for asset in envelope.assets
        ]


class TestForeignZoneByUrl:
    """`page=` also accepts a URL of the foreign page, resolved by URLconf."""

    def test_foreign_url_resolves_to_its_page(self) -> None:
        envelope = (
            Patches(partial_request()).morph(zone="alpha", page="/zoned/").envelope()
        )
        assert (
            envelope.ops[0].html == '<div data-next-zone="alpha"><p>alpha hi</p></div>'
        )

    def test_unresolvable_url_raises_lookup(self) -> None:
        with pytest.raises(LookupError):
            Patches(partial_request()).morph(zone="alpha", page="/no/such/url/")


class TestForeignZoneAuthorization:
    """A foreign page that short-circuits never morphs an empty zone."""

    def test_redirecting_page_raises_not_authorized(self) -> None:
        with pytest.raises(ForeignPageNotAuthorizedError) as exc:
            Patches(partial_request()).morph(zone="alpha", page=_REDIRECTING_PAGE)
        assert exc.value.page_path == _REDIRECTING_PAGE
        assert exc.value.status_code == 302

    def test_no_partial_zone_op_is_recorded_on_denial(self) -> None:
        patches = Patches(partial_request())
        with pytest.raises(ForeignPageNotAuthorizedError):
            patches.morph(zone="alpha", page=_REDIRECTING_PAGE)
        assert patches.envelope().ops == ()


class TestResolvePartialOrigin:
    """`resolve_partial_origin` prefers the host header over the form origin."""

    def test_header_origin_wins(self) -> None:
        request = partial_request(origin="/zoned/", host="/zoned/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        assert origin.page_path == _ZONED_PAGE
        assert origin.source is OriginSource.HEADER

    def test_form_origin_is_the_fallback(self) -> None:
        request = partial_request(origin="/zoned/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        assert origin.page_path == _ZONED_PAGE
        assert origin.source is OriginSource.FORM

    def test_unresolvable_origin_returns_none(self) -> None:
        request = partial_request(origin="/no/such/url/")
        assert resolve_partial_origin(request) is None

    def test_offsite_header_is_rejected_and_falls_back_to_the_form(self) -> None:
        # An off-site X-Next-Origin must not steer the OOB render, the same-site
        # guard drops it and the resolver falls back to the posted form origin.
        request = partial_request(origin="/zoned/", host="//evil.example.com/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        assert origin.page_path == _ZONED_PAGE
        assert origin.source is OriginSource.FORM

    def test_header_that_does_not_resolve_falls_back_to_the_form(self) -> None:
        # A same-site header that names no page leaves the form origin to win,
        # so a stale layer host never blanks the resolved page.
        request = partial_request(origin="/zoned/", host="/no/such/url/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        assert origin.page_path == _ZONED_PAGE
        assert origin.source is OriginSource.FORM

    def test_host_header_morphs_the_named_page(self) -> None:
        request = partial_request(origin="/zoned/", host="/zoned/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        envelope = (
            Patches(request)
            .morph(zone="beta", page=origin.page_path, url_kwargs=origin.url_kwargs)
            .envelope()
        )
        assert envelope.ops[0].target == {"zone": "beta"}


class TestLayerOriginMorphsTheHostNotTheStep:
    """A layer carries its host in `X-Next-Origin`, the OOB morph renders it.

    The end-to-end of the layer seam: the request posts from the wizard
    step page while the layer host rides `X-Next-Origin`. The resolver
    prefers the header, so a `done` handler that morphs `page=origin`
    re-renders the host page's zone, not the step page the form lived on.
    """

    def test_header_host_renders_the_host_pages_zone_body(self) -> None:
        # The form origin names the step page, the layer host header names the
        # owning page, so the resolver must steer the render to the host.
        request = partial_request(origin="/counted/", host="/zoned/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        assert origin.page_path == _ZONED_PAGE
        assert origin.source is OriginSource.HEADER

    def test_done_morphs_the_host_zone_html_over_the_step_page(self) -> None:
        request = partial_request(origin="/counted/", host="/zoned/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        envelope = (
            Patches(request)
            .morph(zone="beta", page=origin.page_path, url_kwargs=origin.url_kwargs)
            .envelope()
        )
        op = envelope.ops[0].as_dict()
        assert op["target"] == {"zone": "beta"}
        # The body is the host page's zone, never the step page the form posts
        # from, so the layer's accept re-GET addresses the right list.
        assert op["html"] == '<section data-next-zone="beta"><p>beta hi</p></section>'
