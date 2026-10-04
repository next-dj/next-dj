"""The hreflang URLs of a path under every language, memoised per URLconf and prefix."""

from collections.abc import Sequence
from typing import Final
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.conf.urls.i18n import is_language_prefix_patterns_used
from django.core.signals import setting_changed
from django.urls import get_script_prefix, get_urlconf, translate_url
from django.utils import translation

from next.caches import LruCache
from next.conf.signals import settings_reloaded


_URLCONF_SETTINGS: Final = frozenset({"ROOT_URLCONF", "LANGUAGES", "LANGUAGE_CODE"})
_ENOUGH: Final = 2

# An empty string marks a code with no route for the path, since no URL is empty.
_translated: Final[LruCache[tuple[str, str, str, str], str]] = LruCache()


def _translate(path: str, code: str, prefix: str) -> str:
    """Translate `path` from its own language, empty when `code` has no route.

    The path resolves under its own language, so the active one does not matter.
    """
    parts = urlsplit(path)
    if not parts.path.startswith(prefix):
        return ""
    local_path = parts.path[len(prefix) - 1 :]
    local = urlunsplit(parts._replace(path=local_path))
    own = translation.get_language_from_path(local_path) or settings.LANGUAGE_CODE
    with translation.override(own):
        translated = translate_url(local, code)
    if translated != local:
        return translated
    return path if code == own else ""


def translated_url(path: str, code: str, *, urlconf: str, prefix: str) -> str | None:
    """Return `path` under the language `code`, `None` when it has no route."""
    key = (urlconf, prefix, path, code)
    url = _translated.get(key)
    if url is None:
        url = _translate(path, code, prefix)
        _translated[key] = url
    return url or None


def hreflang_urls(path: str) -> tuple[tuple[str, str], ...]:
    """Return `path` under every `LANGUAGES` code it translates to, empty below two.

    A URLconf without `i18n_patterns()` has no language prefix, so it returns none.
    """
    urlconf = get_urlconf() or str(getattr(settings, "ROOT_URLCONF", ""))
    if not is_language_prefix_patterns_used(urlconf)[0]:
        return ()
    prefix = get_script_prefix()
    pairs = tuple(
        (code, url)
        for code, _name in settings.LANGUAGES
        if (url := translated_url(path, code, urlconf=urlconf, prefix=prefix))
    )
    return pairs if len(pairs) >= _ENOUGH else ()


def x_default_url(pairs: Sequence[tuple[str, str]]) -> str | None:
    """Return the URL of the default language among `pairs`, the x-default one."""
    return dict(pairs).get(settings.LANGUAGE_CODE)


def forget_translated_urls(**kwargs) -> None:
    """Drop the hreflang memo after a URLconf, router or language change."""
    _translated.clear()


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    if setting in _URLCONF_SETTINGS:
        _translated.clear()


settings_reloaded.connect(forget_translated_urls)
setting_changed.connect(_on_setting_changed)


__all__ = ["forget_translated_urls", "hreflang_urls", "translated_url", "x_default_url"]
