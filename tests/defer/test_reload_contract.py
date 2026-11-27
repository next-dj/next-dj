import json
import logging
import re
import secrets
from contextlib import AbstractContextManager
from pathlib import Path
from typing import ClassVar

import pytest
from django.core.cache import caches
from django.http import HttpRequest, HttpResponse
from django.test import Client, override_settings

from next.components.registry import DeferredComponents
from next.defer import BoundaryTokenError, TokenRejection, boundary_token_rejected
from next.defer.reload import (
    EXPIRED_SIGNAL,
    REFUSED_SIGNAL,
    REJECTED_HEADER,
    RELOAD_HEADER,
)
from next.defer.tokens import (
    VERSION,
    BoundaryClaims,
    BoundaryTarget,
    BoundaryTokenBackend,
    backends as token_backends,
)
from next.defer.tokens.claims import Pairs
from next.testing import capture_signals, reset_failure_logs
from tests.support import routed, write_page
from tests.support.access import COMPONENTS
from tests.support.components import write_component
from tests.support.tokens import fail_cache


pytestmark = pytest.mark.django_db()

_CARD = """
from next.components import component

component.defer("load", props=("sku",))


@component.context("title")
def title(sku: str) -> str:
    return f"card {sku}"
"""

_PUBLIC = "from next import page\n\npage.public()\n"
_WRAPPER = re.compile(
    r'<div id="(?P<id>b-[0-9a-f]{8}-[0-9]+)" data-next-boundary="(?P=id)" '
    r'[^>]*data-next-token="(?P<token>[^"]+)"(?P<rest>[^>]*)>'
)
_CACHED = {"STORAGE": "next.defer.CacheStorage"}


class MemoryTokenBackend(BoundaryTokenBackend):
    """A backend that implements the abstract contract alone, its claims in memory."""

    held: ClassVar[dict[str, BoundaryClaims]] = {}

    def issue(
        self,
        request: HttpRequest,
        claims: BoundaryClaims,
        *,
        tokens: Pairs[object] = (),
    ) -> str:
        token = secrets.token_urlsafe(8)
        self.held[token] = claims
        return token

    def open(
        self, request: HttpRequest, token: str, *, tokens: Pairs[object] = ()
    ) -> BoundaryClaims:
        claims = self.held.get(token)
        if claims is None:
            raise BoundaryTokenError(TokenRejection.STORAGE_MISS)
        return claims

    def settle(
        self, request: HttpRequest, claims: BoundaryClaims, target: BoundaryTarget
    ) -> BoundaryClaims:
        if not target.matches(claims):
            raise BoundaryTokenError(TokenRejection.URL_MISMATCH)
        return claims


@pytest.fixture()
def pages(tmp_path: Path, fresh_deferred_components: DeferredComponents) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text("<html><body>{% template %}</body></html>")
    write_component(
        root, "card", module=_CARD, body="<p>{{ title }}</p>", loading="<i>wait</i>"
    )
    write_page(root, "home", _PUBLIC, body='{% component "card" sku="a" %}')
    return root


@pytest.fixture(autouse=True)
def _quiet_logs() -> None:
    reset_failure_logs()
    caches["default"].clear()


def _routed(pages: Path, **tokens: object) -> AbstractContextManager[None]:
    return routed(pages, COMPONENT_BACKENDS=COMPONENTS, DEFER={"TOKENS": tokens})


def _wrapper(client: Client) -> re.Match[str]:
    found = _WRAPPER.search(client.get("/home/").content.decode())
    assert found is not None
    return found


def _reload(client: Client, found: re.Match[str], token: str = "") -> HttpResponse:
    headers = {
        "X-Next-Request": "1",
        "X-Next-Zone": found["id"],
        "X-Next-Boundary": token or found["token"],
    }
    return client.get("/home/", headers=headers)


def _html(response: HttpResponse) -> str:
    return json.loads(response.content)["ops"][0]["html"]


class TestAbstractContract:
    """A backend that implements `issue`, `open` and `settle` serves a reload."""

    def test_only_the_three_methods_the_core_calls_are_abstract(self) -> None:
        assert BoundaryTokenBackend.__abstractmethods__ == {"issue", "open", "settle"}

    def test_a_minted_token_reloads_through_a_project_backend(
        self, pages: Path
    ) -> None:
        with _routed(pages, BACKEND=f"{__name__}.MemoryTokenBackend"):
            client = Client()
            response = _reload(client, _wrapper(client))
        assert response.status_code == 200
        assert _html(response) == "<p>card a</p>"

    def test_a_project_backend_refusal_answers_one_announced_403(
        self, pages: Path
    ) -> None:
        with _routed(pages, BACKEND=f"{__name__}.MemoryTokenBackend"):
            client = Client()
            found = _wrapper(client)
            MemoryTokenBackend.held[found["token"]] = BoundaryClaims.of(
                "card", "elsewhere", url_path="/home/"
            )
            with capture_signals(boundary_token_rejected) as recorder:
                response = _reload(client, found)
        assert response.status_code == 403
        (event,) = recorder
        assert event.sender is MemoryTokenBackend
        assert event.kwargs["reason"] is TokenRejection.URL_MISMATCH
        assert event.kwargs["component"] == "card"


class TestBodyCarrier:
    """The body carrier answers a reload `POST` that carries the token field."""

    def test_a_token_in_its_form_field_reloads(self, pages: Path) -> None:
        with _routed(pages, OPTIONS={"CARRIER": "body"}):
            client = Client()
            found = _wrapper(client)
            assert 'data-next-carrier="body:_nb"' in found["rest"]
            response = client.post(
                "/home/",
                {"_nb": found["token"]},
                headers={"X-Next-Request": "1", "X-Next-Zone": found["id"]},
            )
        assert response.status_code == 200
        assert _html(response) == "<p>card a</p>"


class TestRefusals:
    """A refused reload stays one answer whatever a receiver or the cache does."""

    def test_a_raising_receiver_leaves_the_403(self, pages: Path) -> None:
        def broken(**kwargs: object) -> None:
            msg = "receiver failed"
            raise RuntimeError(msg)

        boundary_token_rejected.connect(broken, weak=False)
        try:
            with _routed(pages):
                client = Client()
                response = _reload(client, _wrapper(client), "1.1.forged")
        finally:
            boundary_token_rejected.disconnect(broken)
        assert response.status_code == 403
        assert response[RELOAD_HEADER] == REFUSED_SIGNAL

    def test_debug_names_the_reason_of_a_refusal(self, pages: Path) -> None:
        with _routed(pages), override_settings(DEBUG=True):
            client = Client()
            response = _reload(client, _wrapper(client), "1.1.forged")
        assert response.status_code == 403
        assert response[REJECTED_HEADER] == "malformed"
        assert b"its text is not one the token backend mints" in response.content

    def test_production_names_no_reason(self, pages: Path) -> None:
        with _routed(pages):
            client = Client()
            response = _reload(client, _wrapper(client), "1.1.forged")
        assert response.status_code == 403
        assert response.content == b""
        assert REJECTED_HEADER not in response

    def test_a_token_of_another_claims_version_reloads_the_page(
        self, pages: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _routed(pages):
            client = Client()
            found = _wrapper(client)
            monkeypatch.setattr(token_backends, "VERSIONS", (VERSION + 1,))
            with capture_signals(boundary_token_rejected) as recorder:
                response = _reload(client, found)
        assert response.status_code == 409
        assert response[RELOAD_HEADER] == EXPIRED_SIGNAL
        assert recorder.first_for(boundary_token_rejected).kwargs["reason"] is (
            TokenRejection.VERSION
        )

    def test_a_cache_down_at_reload_reloads_the_page_and_logs(
        self,
        pages: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with _routed(pages, OPTIONS=_CACHED):
            client = Client()
            found = _wrapper(client)
            monkeypatch.setattr(caches["default"], "get", fail_cache)
            with caplog.at_level(logging.ERROR, logger="next.defer"):
                response = _reload(client, found)
        assert response.status_code == 409
        assert response[RELOAD_HEADER] == EXPIRED_SIGNAL
        [record] = [r for r in caplog.records if "storage_down" in r.getMessage()]
        assert isinstance(record.exc_info[1].__cause__, ConnectionError)

    def test_a_cache_down_at_mint_renders_the_page_without_a_token(
        self, pages: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(caches["default"], "set", fail_cache)
        with _routed(pages, OPTIONS=_CACHED):
            response = Client().get("/home/")
        html = response.content.decode()
        assert response.status_code == 200
        assert "data-next-token" not in html
        assert "<p>card a</p>" in html
