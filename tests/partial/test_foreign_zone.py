from pathlib import Path

import pytest
from django.test import RequestFactory

from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial import (
    ForeignPageNotAuthorizedError,
    OriginSource,
    Patches,
    resolve_partial_origin,
)
from next.partial.headers import ORIGIN, REQUEST_FLAG


_PAGES_ROOT = Path(__file__).resolve().parent.parent / "site_pages"
_ZONED_PAGE = _PAGES_ROOT / "zoned" / "page.py"
_REDIRECTING_PAGE = _PAGES_ROOT / "redirecting" / "page.py"

_PARTIAL_META = {f"HTTP_{REQUEST_FLAG.upper().replace('-', '_')}": "1"}


def _partial_request(origin: str = "/zoned/", host: str | None = None):
    """Return a partial POST whose form origin resolves to a real page.

    Pass `host` to stamp the `X-Next-Origin` header with a host page URL.
    """
    meta = dict(_PARTIAL_META)
    if host is not None:
        meta[f"HTTP_{ORIGIN.upper().replace('-', '_')}"] = host
    return RequestFactory().post(
        "/_next/form/x/", data={ORIGIN_FIELD_NAME: origin}, **meta
    )


class TestForeignZoneByPath:
    """`morph(zone=, page=)` renders the zone of a page named by its path."""

    def test_foreign_page_path_morphs_its_zone(self) -> None:
        envelope = (
            Patches(_partial_request()).morph(zone="alpha", page=_ZONED_PAGE).envelope()
        )
        op = envelope.ops[0].as_dict()
        assert op["op"] == "morph"
        assert op["target"] == {"zone": "alpha"}
        assert op["html"] == '<div data-next-zone="alpha"><p>alpha hi</p></div>'

    def test_foreign_zone_assets_travel_in_the_envelope(self) -> None:
        envelope = (
            Patches(_partial_request()).morph(zone="alpha", page=_ZONED_PAGE).envelope()
        )
        assert {"kind": "css", "url": "/static/next/zoned.css"} in [
            asset.as_dict() for asset in envelope.assets
        ]


class TestForeignZoneByUrl:
    """`page=` also accepts a URL of the foreign page, resolved by URLconf."""

    def test_foreign_url_resolves_to_its_page(self) -> None:
        envelope = (
            Patches(_partial_request()).morph(zone="alpha", page="/zoned/").envelope()
        )
        assert (
            envelope.ops[0].html == '<div data-next-zone="alpha"><p>alpha hi</p></div>'
        )

    def test_unresolvable_url_raises_lookup(self) -> None:
        with pytest.raises(LookupError):
            Patches(_partial_request()).morph(zone="alpha", page="/no/such/url/")


class TestForeignZoneAuthorization:
    """A foreign page that short-circuits never morphs an empty zone."""

    def test_redirecting_page_raises_not_authorized(self) -> None:
        with pytest.raises(ForeignPageNotAuthorizedError) as exc:
            Patches(_partial_request()).morph(zone="alpha", page=_REDIRECTING_PAGE)
        assert exc.value.page_path == _REDIRECTING_PAGE
        assert exc.value.status_code == 302

    def test_no_partial_zone_op_is_recorded_on_denial(self) -> None:
        patches = Patches(_partial_request())
        with pytest.raises(ForeignPageNotAuthorizedError):
            patches.morph(zone="alpha", page=_REDIRECTING_PAGE)
        assert patches.envelope().ops == ()


class TestResolvePartialOrigin:
    """`resolve_partial_origin` prefers the host header over the form origin."""

    def test_header_origin_wins(self) -> None:
        request = _partial_request(origin="/zoned/", host="/zoned/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        assert origin.page_path == _ZONED_PAGE
        assert origin.source is OriginSource.HEADER

    def test_form_origin_is_the_fallback(self) -> None:
        request = _partial_request(origin="/zoned/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        assert origin.page_path == _ZONED_PAGE
        assert origin.source is OriginSource.FORM

    def test_unresolvable_origin_returns_none(self) -> None:
        request = _partial_request(origin="/no/such/url/")
        assert resolve_partial_origin(request) is None

    def test_offsite_header_is_rejected_and_falls_back_to_the_form(self) -> None:
        # An off-site X-Next-Origin must not steer the OOB render, the same-site
        # guard drops it and the resolver falls back to the posted form origin.
        request = _partial_request(origin="/zoned/", host="//evil.example.com/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        assert origin.page_path == _ZONED_PAGE
        assert origin.source is OriginSource.FORM

    def test_header_that_does_not_resolve_falls_back_to_the_form(self) -> None:
        # A same-site header that names no page leaves the form origin to win,
        # so a stale layer host never blanks the resolved page.
        request = _partial_request(origin="/zoned/", host="/no/such/url/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        assert origin.page_path == _ZONED_PAGE
        assert origin.source is OriginSource.FORM

    def test_host_header_morphs_the_named_page(self) -> None:
        request = _partial_request(origin="/zoned/", host="/zoned/")
        origin = resolve_partial_origin(request)
        assert origin is not None
        envelope = (
            Patches(request)
            .morph(zone="beta", page=origin.page_path, url_kwargs=origin.url_kwargs)
            .envelope()
        )
        assert envelope.ops[0].target == {"zone": "beta"}
