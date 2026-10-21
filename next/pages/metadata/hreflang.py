"""The hreflang URLs of a path under every language, memoised per route."""

from collections.abc import Sequence
from typing import Final
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

from django.conf import settings
from django.conf.urls.i18n import is_language_prefix_patterns_used
from django.urls import (
    NoReverseMatch,
    Resolver404,
    ResolverMatch,
    get_script_prefix,
    get_urlconf,
    resolve,
    reverse,
)
from django.utils import translation

from next.caches import LruCache


_URLCONF_SETTINGS: Final = frozenset({"ROOT_URLCONF", "LANGUAGES", "LANGUAGE_CODE"})
_ENOUGH: Final = 2

type _RouteKey = tuple[
    str, str, str, tuple[object, ...], tuple[tuple[str, object], ...]
]
type _PathKey = tuple[str, str, str]
type _Paths = tuple[tuple[str, str], ...]

# Keyed by route, so every language variant of a page shares one entry.
_translated: Final[LruCache[_RouteKey | _PathKey, _Paths]] = LruCache()


def _own_language(local_path: str) -> str:
    """Return the language the prefix of `local_path` names, the default without one."""
    return translation.get_language_from_path(local_path) or settings.LANGUAGE_CODE


def _named_match(local_path: str, own: str, urlconf: str) -> ResolverMatch | None:
    """Resolve `local_path` under the language `own`, `None` when no route matches."""
    with translation.override(own):
        try:
            return resolve(unquote(local_path), urlconf)
        except Resolver404:
            return None


def _reverse(match: ResolverMatch, code: str, urlconf: str) -> str:
    """Reverse the route of `match` under the language `code`, empty when it cannot."""
    with translation.override(code):
        try:
            return reverse(
                match.view_name, args=match.args, kwargs=match.kwargs, urlconf=urlconf
            )
        except NoReverseMatch:
            return ""


def _route_paths(
    match: ResolverMatch, parts: SplitResult, urlconf: str, prefix: str
) -> _Paths:
    """Return every `LANGUAGES` code with the path of the route of `match`, memoised.

    URL arguments keep the route pattern order, so one route always builds the same key.
    """
    key: _RouteKey | _PathKey = (
        urlconf,
        prefix,
        match.view_name,
        tuple(match.args),
        tuple(match.kwargs.items()),
    )
    try:
        found = _translated.get(key)
    except TypeError:
        key = (urlconf, prefix, parts.path)
        found = _translated.get(key)
    if found is None:
        found = tuple(
            (code, _reverse(match, code, urlconf)) for code, _name in settings.LANGUAGES
        )
        _translated[key] = found
    return found


def _own_code(paths: _Paths, path: str) -> str | None:
    """Return the one code whose route path is `path`, `None` for none or several.

    Each `i18n_patterns()` language reverses to its own path, which names the language.
    """
    owners = [code for code, found in paths if found == path]
    return owners[0] if len(owners) == 1 else None


def _translations(
    path: str, urlconf: str, prefix: str, match: ResolverMatch | None
) -> tuple[tuple[str, str], ...]:
    """Return `path` under every `LANGUAGES` code its route reverses under, in order.

    A resolved `match` saves the resolve, and a code repeating the own path is left out.
    """
    parts = urlsplit(path)
    if not parts.path.startswith(prefix):
        return ()
    local_path = parts.path[len(prefix) - 1 :]
    own = None
    if match is None:
        own = _own_language(local_path)
        match = _named_match(local_path, own, urlconf)
    if match is None or match.url_name is None:
        own = own or _own_language(local_path)
        return tuple((code, path) for code, _name in settings.LANGUAGES if code == own)
    paths = _route_paths(match, parts, urlconf, prefix)
    if own is None:
        own = _own_code(paths, parts.path) or _own_language(local_path)
    own_path = dict(paths).get(own)
    if own_path is None:
        own_path = _reverse(match, own, urlconf)
    pairs: list[tuple[str, str]] = []
    for code, translated in paths:
        if code == own:
            pairs.append((code, _with_path(parts, own_path) if own_path else path))
        elif translated and translated != own_path:
            pairs.append((code, _with_path(parts, translated)))
    return tuple(pairs)


def _with_path(parts: SplitResult, path: str) -> str:
    """Return the URL of `parts` under `path`, `path` itself when `parts` is bare."""
    if parts.scheme or parts.netloc or parts.query or parts.fragment:
        return urlunsplit(parts._replace(path=path))
    return path


def translated_url(path: str, code: str, *, urlconf: str, prefix: str) -> str | None:
    """Return `path` under the language `code`, `None` when it has no route."""
    return dict(_translations(path, urlconf, prefix, None)).get(code)


def hreflang_urls(
    path: str, *, match: ResolverMatch | None = None
) -> tuple[tuple[str, str], ...]:
    """Return `path` under every `LANGUAGES` code it translates to, empty below two.

    `match` is the caller's resolve of `path`, and no `i18n_patterns()` means no result.
    """
    urlconf = get_urlconf() or str(getattr(settings, "ROOT_URLCONF", ""))
    if not is_language_prefix_patterns_used(urlconf)[0]:
        return ()
    pairs = _translations(path, urlconf, get_script_prefix(), match)
    return pairs if len(pairs) >= _ENOUGH else ()


def x_default_url(pairs: Sequence[tuple[str, str]]) -> str | None:
    """Return the URL of the default language among `pairs`, the x-default one."""
    return dict(pairs).get(settings.LANGUAGE_CODE)


def forget_translated_urls(**kwargs) -> None:
    """Drop the hreflang memo after a URLconf, router or language change."""
    _translated.clear()


def forget_urlconf_translations(*, setting: str, **kwargs) -> None:
    if setting in _URLCONF_SETTINGS:
        _translated.clear()


__all__ = ["forget_translated_urls", "hreflang_urls", "translated_url", "x_default_url"]
