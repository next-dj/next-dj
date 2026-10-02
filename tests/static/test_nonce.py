from unittest.mock import patch

import django
import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings
from django.utils.functional import SimpleLazyObject

from next.static.nonce import (
    CSP_MIDDLEWARE,
    CSP_NONCE_ATTR,
    NONCE_ATTR,
    django_get_nonce,
    nonce_active,
    nonce_enabled,
    nonce_minted,
    request_nonce,
    resolve_nonce,
)
from next.testing import override_next_settings
from tests.support import build_mock_http_request


DJANGO_6 = django.VERSION >= (6, 0)


class TestDjangoGetNonce:
    """Django's own `get_nonce` is used where the installed Django ships it."""

    @pytest.mark.skipif(not DJANGO_6, reason="django.middleware.csp is Django 6.0+")
    def test_django_six_ships_it(self) -> None:
        assert django_get_nonce is not None
        assert django_get_nonce(RequestFactory().get("/")) is None

    @pytest.mark.skipif(DJANGO_6, reason="Django 6.0+ ships django.middleware.csp")
    def test_django_five_ships_none(self) -> None:
        assert django_get_nonce is None


class TestRequestNonce:
    """The default resolver reads django-csp, then Django's CSP middleware."""

    def test_django_csp_sets_it_on_the_request(self) -> None:
        request = RequestFactory().get("/")
        request.csp_nonce = SimpleLazyObject(lambda: "from-csp")
        assert request_nonce(request) == "from-csp"

    def test_django_middleware_mints_it(self) -> None:
        request = RequestFactory().get("/")
        with patch(
            "next.static.nonce.django_get_nonce", lambda _request: "from-django"
        ):
            assert request_nonce(request) == "from-django"

    def test_no_middleware_answers_none(self) -> None:
        with patch("next.static.nonce.django_get_nonce", None):
            assert request_nonce(RequestFactory().get("/")) is None

    def test_an_empty_nonce_answers_none(self) -> None:
        request = RequestFactory().get("/")
        request.csp_nonce = ""
        assert request_nonce(request) is None


class TestResolveNonce:
    """`CSP_NONCE` switches the nonce on, read once per request."""

    def test_the_nonce_is_on_by_default(self) -> None:
        assert nonce_enabled() is True

    def test_the_request_nonce_is_read_once_per_request(self) -> None:
        request = RequestFactory().get("/")
        request.csp_nonce = "n1"
        assert resolve_nonce(request) == "n1"
        request.csp_nonce = "n2"
        assert resolve_nonce(request) == "n1"
        assert getattr(request, NONCE_ATTR) == "n1"

    def test_false_turns_the_nonce_off(self) -> None:
        request = RequestFactory().get("/")
        request.csp_nonce = "n1"
        with override_next_settings(CSP_NONCE=False):
            assert nonce_enabled() is False
            assert resolve_nonce(request) is None

    def test_a_request_without_a_nonce_is_held_as_none(self) -> None:
        request = RequestFactory().get("/")
        with patch("next.static.nonce.django_get_nonce", None):
            assert resolve_nonce(request) is None
            assert resolve_nonce(request) is None
        assert getattr(request, NONCE_ATTR) is None

    def test_a_nonce_a_tag_read_counts_as_minted(self) -> None:
        request = RequestFactory().get("/")
        request.csp_nonce = "n1"
        assert not nonce_minted(request)
        resolve_nonce(request)
        assert nonce_minted(request)

    def test_no_nonce_leaves_the_render_shareable(self) -> None:
        request = RequestFactory().get("/")
        request.csp_nonce = "n1"
        with override_next_settings(CSP_NONCE=False):
            resolve_nonce(request)
        assert not nonce_minted(request)

    def test_a_nonce_the_middleware_minted_counts_whoever_read_it(self) -> None:
        request = RequestFactory().get("/")
        setattr(request, CSP_NONCE_ATTR, "n1")
        assert nonce_minted(request)

    def test_only_a_request_is_asked(self) -> None:
        request = build_mock_http_request()
        request.csp_nonce = "n0"
        assert resolve_nonce(request) == "n0"
        assert resolve_nonce(None) is None
        assert resolve_nonce(object()) is None


class TestNonceActive:
    """A nonce reaches the tags only with `CSP_NONCE` on and a minting middleware."""

    @pytest.mark.parametrize("middleware", CSP_MIDDLEWARE)
    def test_either_middleware_activates_it(self, middleware) -> None:
        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, middleware]):
            assert nonce_active() is True
            with override_next_settings(CSP_NONCE=False):
                assert nonce_active() is False

    def test_no_middleware_leaves_it_off(self) -> None:
        assert nonce_active() is False
        with override_settings(MIDDLEWARE=None):
            assert nonce_active() is False
