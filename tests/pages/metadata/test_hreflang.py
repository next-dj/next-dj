from collections.abc import Callable, Iterator

import pytest
from django.core.signals import setting_changed
from django.test import override_settings
from django.urls import clear_script_prefix, set_script_prefix, set_urlconf
from django.utils import translation

import next.pages.metadata.hreflang as hreflang_module
from next.conf.signals import settings_reloaded
from next.pages.metadata.hreflang import hreflang_urls, translated_url, x_default_url
from next.urls import RouterManager
from next.urls.signals import router_reloaded
from tests.support import (
    I18N,
    I18N_PREFIXED_URLCONF,
    I18N_ROUTED,
    I18N_URLCONF,
    LANGUAGES,
    NAMESPACED_URLCONF,
    record_calls,
)


THREE = [*LANGUAGES, ("fr", "French")]


def _changed(setting: str) -> None:
    setting_changed.send(sender=None, setting=setting, value=None, enter=True)


@pytest.fixture()
def i18n() -> Iterator[None]:
    with override_settings(**I18N_ROUTED):
        yield


@pytest.mark.usefixtures("i18n")
class TestHreflangUrls:
    """A path lists every language its route answers, or nothing below two."""

    def test_every_language_is_listed_in_settings_order(self) -> None:
        assert hreflang_urls("/headed/") == (("en", "/headed/"), ("de", "/de/headed/"))

    def test_a_prefixed_path_translates_from_its_own_language(self) -> None:
        assert hreflang_urls("/de/headed/") == (
            ("en", "/headed/"),
            ("de", "/de/headed/"),
        )

    def test_the_active_language_does_not_poison_the_memo(self) -> None:
        with translation.override("de"):
            assert hreflang_urls("/headed/") == (
                ("en", "/headed/"),
                ("de", "/de/headed/"),
            )
        with translation.override("en"):
            assert hreflang_urls("/headed/")[1] == ("de", "/de/headed/")

    def test_an_unrouted_path_lists_nothing(self) -> None:
        assert hreflang_urls("/nowhere/") == ()
        assert (
            translated_url("/nowhere/", "de", urlconf=I18N_URLCONF, prefix="/") is None
        )
        assert translated_url("/nowhere/", "en", urlconf=I18N_URLCONF, prefix="/") == (
            "/nowhere/"
        )

    @override_settings(LANGUAGES=THREE)
    def test_a_code_without_a_route_is_dropped(self, monkeypatch) -> None:
        def translate(url: str, code: str) -> str:
            return url if code == "fr" else f"/{code}{url}"

        monkeypatch.setattr(hreflang_module, "translate_url", translate)
        assert hreflang_urls("/headed/") == (
            ("en", "/en/headed/"),
            ("de", "/de/headed/"),
        )

    def test_the_thread_urlconf_is_honoured(self) -> None:
        with override_settings(ROOT_URLCONF=NAMESPACED_URLCONF):
            set_urlconf(I18N_URLCONF)
            try:
                pairs = hreflang_urls("/headed/")
            finally:
                set_urlconf(None)
        assert pairs[1] == ("de", "/de/headed/")

    def test_translations_are_memoised(self, monkeypatch) -> None:
        calls = record_calls(monkeypatch, hreflang_module, "translate_url")
        hreflang_urls("/headed/")
        hreflang_urls("/headed/")
        assert [call.args for call in calls] == [("/headed/", "en"), ("/headed/", "de")]

    def test_the_default_language_is_the_x_default(self) -> None:
        assert x_default_url(hreflang_urls("/headed/")) == "/headed/"
        assert x_default_url((("de", "/de/"),)) is None


@pytest.mark.usefixtures("i18n")
class TestScriptPrefix:
    """A path below the script prefix translates, one outside it translates to none."""

    @pytest.fixture(autouse=True)
    def _prefix(self) -> Iterator[None]:
        set_script_prefix("/app/")
        yield
        clear_script_prefix()

    def test_a_prefixed_path_keeps_the_prefix(self) -> None:
        assert hreflang_urls("/app/headed/") == (
            ("en", "/app/headed/"),
            ("de", "/app/de/headed/"),
        )

    def test_a_path_outside_the_prefix_lists_nothing(self) -> None:
        assert hreflang_urls("https://other.example/headed/") == ()

    def test_the_memo_tells_script_prefixes_apart(self) -> None:
        prefixed = hreflang_urls("/app/headed/")
        clear_script_prefix()
        assert hreflang_urls("/app/headed/") == ()
        assert prefixed[1] == ("de", "/app/de/headed/")


class TestWithoutLanguagePatterns:
    """A URLconf without `i18n_patterns()` has no alternates to list."""

    def test_nothing_is_listed(self) -> None:
        assert hreflang_urls("/headed/") == ()


class TestInvalidation:
    """The memo drops on a reload, a URLconf or language change and a router reload."""

    @pytest.mark.parametrize(
        ("invalidate", "translations"),
        [
            (lambda: settings_reloaded.send(sender=None), 4),
            (lambda: router_reloaded.send(sender=RouterManager), 4),
            (lambda: _changed("ROOT_URLCONF"), 4),
            (lambda: _changed("LANGUAGES"), 4),
            (lambda: _changed("LANGUAGE_CODE"), 4),
            (lambda: _changed("DEBUG"), 2),
        ],
        ids=[
            "settings_reloaded",
            "router_reloaded",
            "urlconf",
            "languages",
            "language_code",
            "other_setting",
        ],
    )
    @override_settings(**I18N_ROUTED)
    def test_a_second_read_translates_again_only_after_a_drop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        invalidate: Callable[[], object],
        translations: int,
    ) -> None:
        calls = record_calls(monkeypatch, hreflang_module, "translate_url")
        hreflang_urls("/headed/")
        invalidate()
        hreflang_urls("/headed/")
        assert len(calls) == translations


@pytest.fixture()
def prefixed_i18n() -> Iterator[None]:
    with override_settings(**I18N, ROOT_URLCONF=I18N_PREFIXED_URLCONF):
        yield


@pytest.mark.usefixtures("prefixed_i18n")
class TestPrefixedDefaultLanguage:
    """With the default language prefixed too, every URL carries its own prefix."""

    def test_every_language_is_listed_under_its_prefix(self) -> None:
        listed = (("en", "/en/headed/"), ("de", "/de/headed/"))
        assert hreflang_urls("/en/headed/") == listed
        assert hreflang_urls("/de/headed/") == listed

    def test_a_bare_path_routes_nothing_to_translate(self) -> None:
        assert hreflang_urls("/headed/") == ()
