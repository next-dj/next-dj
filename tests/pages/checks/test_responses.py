from pathlib import Path

import pytest
from django.middleware.http import ConditionalGetMiddleware
from django.middleware.security import SecurityMiddleware
from django.test import override_settings

from next.checks import reset_check_caches
from next.pages.checks import (
    check_conditional_get_order,
    check_csrf_delivery,
    check_csrf_endpoint_reversible,
    check_csrf_in_session,
    check_page_response_declarations,
    check_shared_page_responses,
)
from tests.support import (
    FEED_URLCONF,
    I18N,
    I18N_URLCONF,
    check_ids,
    routed,
    write_page,
)


LOCALE = {
    "MIDDLEWARE": [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.locale.LocaleMiddleware",
        "django.middleware.common.CommonMiddleware",
    ],
    **I18N,
}


def _tree(tmp_path: Path, source: str, layout: str = "{% template %}") -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(layout)
    write_page(root, "", source)
    return root


@pytest.fixture(autouse=True)
def _fresh_run():
    reset_check_caches()
    yield
    reset_check_caches()


class TestDeclarations:
    """`next.E131` names a `cache` or `headers` the response cannot carry."""

    def test_valid_declarations_are_silent(self, tmp_path) -> None:
        source = (
            "template = 'x'\n"
            "cache = {'public': True, 'max_age': 60}\n"
            "headers = {'X-Team': 'web', 'X-Old': None}\n"
        )
        with routed(_tree(tmp_path, source)):
            assert check_page_response_declarations() == []

    @pytest.mark.parametrize(
        ("source", "fragment"),
        [
            ("cache = -1\n", "cache = -1, and the age is negative"),
            ("cache = {'public': True, 'no_store': True}\n", "public contradicts"),
            ("headers = {'Vary': 'Cookie'}\n", "Vary is set by cache"),
            ("headers = {'Surrogate-Control': 'max-age=9'}\n", "declare the caching"),
            ("headers = {'Set-Cookie': 'a=b'}\n", "Set-Cookie is set by the"),
            ("headers = {'X-A': 'a\\nb'}\n", "must be ASCII text on one line"),
        ],
        ids=["negative", "contradiction", "vary", "cdn", "forbidden", "break"],
    )
    def test_an_unusable_declaration_is_e131(self, tmp_path, source, fragment) -> None:
        with routed(_tree(tmp_path, "template = 'x'\n" + source)):
            [error] = check_page_response_declarations()
        assert error.id == "next.E131"
        assert fragment in error.msg
        assert ";" not in error.msg


class TestCsrfDelivery:
    """`next.E132` names a `CSRF_DELIVERY` that is no delivery mode."""

    @pytest.mark.parametrize("value", ["auto", "eager", "lazy", 3])
    def test_a_known_mode_or_another_type_is_silent(self, value) -> None:
        with override_settings(NEXT_FRAMEWORK={"CSRF_DELIVERY": value}):
            assert check_csrf_delivery() == []

    def test_an_unknown_mode_is_e132(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"CSRF_DELIVERY": "never"}):
            [error] = check_csrf_delivery()
        assert error.id == "next.E132"
        assert "'auto', 'eager', 'lazy'" in error.msg


class TestSharedPages:
    """`next.W113` and `next.W114` warn about a page a CDN may hold."""

    def test_a_csrf_token_tag_is_w122(self, tmp_path) -> None:
        source = "template = '{% csrf_token %}'\ncache = {'s_maxage': 60}\n"
        with routed(_tree(tmp_path, source)):
            assert check_ids(check_shared_page_responses()) == ["next.W113"]

    def test_a_callable_cache_counts_as_possibly_shared(self, tmp_path) -> None:
        source = "template = '{% csrf_token %}'\n\ndef cache():\n    return 60\n"
        with routed(_tree(tmp_path, source)):
            [warning] = check_shared_page_responses()
        assert warning.id == "next.W113"
        assert "page.py (callable cache) declares" in warning.msg

    def test_locale_middleware_outside_i18n_patterns_is_w123(self, tmp_path) -> None:
        with (
            routed(_tree(tmp_path, "template = 'x'\ncache = 60\n")),
            override_settings(**LOCALE),
        ):
            assert check_ids(check_shared_page_responses()) == ["next.W114"]

    def test_prefixed_pages_mix_no_languages(self, tmp_path) -> None:
        with (
            routed(
                _tree(tmp_path, "template = 'x'\ncache = 60\n"), urlconf=I18N_URLCONF
            ),
            override_settings(**LOCALE),
        ):
            assert check_shared_page_responses() == []


def _pages(tmp_path: Path, *sources: str) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    for index, source in enumerate(sources):
        write_page(root, f"p{index}", "template = 'x'\n" + source)
    return root


class TestCsrfInSession:
    """`next.W121` names `CSRF_USE_SESSIONS` taking every shared page private."""

    def test_csrf_in_its_cookie_is_silent(self, tmp_path) -> None:
        with routed(_pages(tmp_path, "cache = 60\n")):
            assert check_csrf_in_session() == []

    def test_csrf_in_the_session_is_w131(self, tmp_path) -> None:
        with (
            routed(_pages(tmp_path, "cache = {'s_maxage': 60}\n")),
            override_settings(CSRF_USE_SESSIONS=True),
        ):
            [warning] = check_csrf_in_session()
        assert warning.id == "next.W121"
        assert "p0" in warning.msg
        assert "CSRF_USE_SESSIONS" in warning.msg

    def test_no_shared_page_is_silent(self, tmp_path) -> None:
        sources = ("", "cache = False\n", "cache = {'max_age': 60}\n")
        with (
            routed(_pages(tmp_path, *sources)),
            override_settings(CSRF_USE_SESSIONS=True),
        ):
            assert check_csrf_in_session() == []

    def test_a_callable_cache_is_listed_and_marked(self, tmp_path) -> None:
        with (
            routed(_pages(tmp_path, "def cache():\n    return False\n")),
            override_settings(CSRF_USE_SESSIONS=True),
        ):
            [warning] = check_csrf_in_session()
        assert "page.py (callable cache)" in warning.msg

    def test_a_long_list_of_pages_is_counted(self, tmp_path) -> None:
        with (
            routed(_pages(tmp_path, *["cache = 60\n"] * 5)),
            override_settings(CSRF_USE_SESSIONS=True),
        ):
            [warning] = check_csrf_in_session()
        assert "and 2 more" in warning.msg


CONDITIONAL = "django.middleware.http.ConditionalGetMiddleware"
SESSIONS = "django.contrib.sessions.middleware.SessionMiddleware"
SECURITY = "django.middleware.security.SecurityMiddleware"
GUARD = "next.middleware.SharedCacheGuardMiddleware"
UPDATE_CACHE = "django.middleware.cache.UpdateCacheMiddleware"


class QuietSecurity(SecurityMiddleware):
    """A project subclass of a middleware that sets no cookie."""


class Conditional(ConditionalGetMiddleware):
    """A project subclass of `ConditionalGetMiddleware`."""


class TestConditionalGetOrder:
    """`next.W124` names a cookie-setting middleware wrapping `ConditionalGet`."""

    @pytest.mark.parametrize(
        "middleware",
        [[SESSIONS], [CONDITIONAL, SESSIONS], [SECURITY, CONDITIONAL, SESSIONS]],
        ids=["absent", "outermost", "below_security"],
    )
    def test_a_safe_order_is_silent(self, tmp_path, middleware) -> None:
        with (
            routed(_pages(tmp_path, "cache = 60\n")),
            override_settings(MIDDLEWARE=middleware),
        ):
            assert check_conditional_get_order() == []

    def test_a_cookie_middleware_above_it_is_w134(self, tmp_path) -> None:
        with (
            routed(_pages(tmp_path, "cache = 60\n")),
            override_settings(MIDDLEWARE=[SECURITY, SESSIONS, CONDITIONAL]),
        ):
            [warning] = check_conditional_get_order()
        assert warning.id == "next.W124"
        assert warning.msg.startswith(f"settings.MIDDLEWARE lists {SESSIONS} above")
        assert "after the page made its response private" in warning.msg
        assert "p0" in warning.msg
        assert GUARD in warning.hint

    def test_a_subclass_is_read_as_its_base(self, tmp_path) -> None:
        middleware = [f"{__name__}.QuietSecurity", SESSIONS, f"{__name__}.Conditional"]
        with (
            routed(_pages(tmp_path, "cache = 60\n")),
            override_settings(MIDDLEWARE=middleware),
        ):
            [warning] = check_conditional_get_order()
        assert f"lists {SESSIONS} above" in warning.msg

    def test_an_entry_that_does_not_import_counts_as_cookie_setting(
        self, tmp_path
    ) -> None:
        with (
            routed(_pages(tmp_path, "cache = 60\n")),
            override_settings(MIDDLEWARE=["missing.Middleware", CONDITIONAL]),
        ):
            [warning] = check_conditional_get_order()
        assert "missing.Middleware" in warning.msg

    @pytest.mark.parametrize(
        "middleware",
        [[GUARD, SESSIONS, CONDITIONAL], [UPDATE_CACHE, GUARD, SESSIONS, CONDITIONAL]],
        ids=["first", "below_update_cache"],
    )
    def test_the_guard_in_front_silences_it(self, tmp_path, middleware) -> None:
        with (
            routed(_pages(tmp_path, "cache = 60\n")),
            override_settings(MIDDLEWARE=middleware),
        ):
            assert check_conditional_get_order() == []

    @pytest.mark.parametrize(
        "middleware",
        [[SESSIONS, GUARD, CONDITIONAL], [GUARD, UPDATE_CACHE, SESSIONS, CONDITIONAL]],
        ids=["below_a_cookie", "above_update_cache"],
    )
    def test_the_guard_further_down_does_not(self, tmp_path, middleware) -> None:
        # Above `UpdateCacheMiddleware` the guard runs after the public copy is
        # stored, so the cache replays the cookie to everyone.
        with (
            routed(_pages(tmp_path, "cache = 60\n")),
            override_settings(MIDDLEWARE=middleware),
        ):
            assert check_ids(check_conditional_get_order()) == ["next.W124"]

    def test_no_shared_page_is_silent(self, tmp_path) -> None:
        with (
            routed(_pages(tmp_path, "cache = False\n")),
            override_settings(MIDDLEWARE=[SESSIONS, CONDITIONAL]),
        ):
            assert check_conditional_get_order() == []


class TestCsrfEndpoint:
    """`next.E148` names a deferred token whose endpoint ROOT_URLCONF does not route."""

    def test_lazy_delivery_without_the_endpoint_is_e148(self, tmp_path) -> None:
        with routed(_pages(tmp_path), urlconf=FEED_URLCONF, CSRF_DELIVERY="lazy"):
            [error] = check_csrf_endpoint_reversible()
        assert error.id == "next.E148"
        assert "'lazy'" in error.msg
        assert "include('next.urls')" in error.hint

    def test_auto_delivery_without_a_shared_page_is_silent(self, tmp_path) -> None:
        with routed(_pages(tmp_path, "cache = False\n"), urlconf=FEED_URLCONF):
            assert check_csrf_endpoint_reversible() == []

    def test_auto_delivery_with_a_shared_page_is_e148(self, tmp_path) -> None:
        with routed(_pages(tmp_path, "cache = 60\n"), urlconf=FEED_URLCONF):
            assert check_ids(check_csrf_endpoint_reversible()) == ["next.E148"]

    def test_eager_delivery_needs_no_endpoint(self, tmp_path) -> None:
        with routed(
            _pages(tmp_path, "cache = 60\n"),
            urlconf=FEED_URLCONF,
            CSRF_DELIVERY="eager",
        ):
            assert check_csrf_endpoint_reversible() == []

    def test_a_routed_endpoint_is_silent(self, tmp_path) -> None:
        with routed(_pages(tmp_path, "cache = 60\n"), CSRF_DELIVERY="lazy"):
            assert check_csrf_endpoint_reversible() == []
