from pathlib import Path

import pytest
from django.test import override_settings

from next.checks import reset_check_caches
from next.pages.checks import (
    check_conditional_get_order,
    check_csrf_delivery,
    check_csrf_in_session,
    check_page_response_declarations,
    check_shared_page_responses,
)
from tests.support import I18N, I18N_URLCONF, check_ids, routed, write_page


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
            ("headers = {'Vary': 'Cookie'}\n", "Vary follows cache"),
            ("headers = {'Surrogate-Control': 'max-age=9'}\n", "declare the caching"),
            ("headers = {'Set-Cookie': 'a=b'}\n", "Set-Cookie belongs to the"),
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
    """`next.W122` and `next.W123` warn about a page a CDN may hold."""

    def test_a_csrf_token_tag_is_w122(self, tmp_path) -> None:
        source = "template = '{% csrf_token %}'\ncache = {'s_maxage': 60}\n"
        with routed(_tree(tmp_path, source)):
            assert check_ids(check_shared_page_responses()) == ["next.W122"]

    def test_a_callable_cache_is_not_judged(self, tmp_path) -> None:
        source = "template = '{% csrf_token %}'\n\ndef cache():\n    return 60\n"
        with routed(_tree(tmp_path, source)):
            assert check_shared_page_responses() == []

    def test_locale_middleware_outside_i18n_patterns_is_w123(self, tmp_path) -> None:
        with (
            routed(_tree(tmp_path, "template = 'x'\ncache = 60\n")),
            override_settings(**LOCALE),
        ):
            assert check_ids(check_shared_page_responses()) == ["next.W123"]

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
    """`next.W131` names `CSRF_USE_SESSIONS` taking every shared page private."""

    def test_csrf_in_its_cookie_is_silent(self, tmp_path) -> None:
        with routed(_pages(tmp_path, "cache = 60\n")):
            assert check_csrf_in_session() == []

    def test_csrf_in_the_session_is_w131(self, tmp_path) -> None:
        with (
            routed(_pages(tmp_path, "cache = {'s_maxage': 60}\n")),
            override_settings(CSRF_USE_SESSIONS=True),
        ):
            [warning] = check_csrf_in_session()
        assert warning.id == "next.W131"
        assert "p0" in warning.msg
        assert "CSRF_USE_SESSIONS" in warning.msg

    def test_no_shared_page_is_silent(self, tmp_path) -> None:
        sources = ("", "cache = False\n", "def cache():\n    return 60\n")
        with (
            routed(_pages(tmp_path, *sources)),
            override_settings(CSRF_USE_SESSIONS=True),
        ):
            assert check_csrf_in_session() == []

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


class TestConditionalGetOrder:
    """`next.W134` names a cookie-setting middleware wrapping `ConditionalGet`."""

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
        assert warning.id == "next.W134"
        assert warning.msg.startswith(f"{SESSIONS} sits above")
        assert "p0" in warning.msg

    def test_no_shared_page_is_silent(self, tmp_path) -> None:
        with (
            routed(_pages(tmp_path, "cache = False\n")),
            override_settings(MIDDLEWARE=[SESSIONS, CONDITIONAL]),
        ):
            assert check_conditional_get_order() == []
