"""The path rule the active-link pass and the breadcrumb trail mark links by."""

import functools
import re
from collections.abc import Callable, Collection
from typing import Final
from urllib.parse import quote, unquote

from django.conf import settings
from django.conf.urls.i18n import is_language_prefix_patterns_used
from django.urls import get_script_prefix, get_urlconf
from django.utils.translation import to_language

from next.caches import pure_cache
from next.wire import CURRENT_ATTR


PAGE_MARK: Final = f' aria-current="page" {CURRENT_ATTR}="page"'
"""The attributes a link to the current page carries."""

ANCESTOR_MARK: Final = f' {CURRENT_ATTR}="ancestor"'
"""The attributes a link to an ancestor of the current page carries."""

_SEGMENT_SAFE: Final = "!$&'()*+,;=:@"
_PLAIN: Final = rf"[A-Za-z0-9_.~{re.escape(_SEGMENT_SAFE)}-]"
_CANONICAL: Final = re.compile(rf"{_PLAIN}*+")
_SETTLED: Final = re.compile(rf"(?:/(?!\.\.?(?:/|\Z)){_PLAIN}++)*+/?")
_DOT: Final = frozenset({".", "%2e"})
_DOTDOT: Final = frozenset({"..", ".%2e", "%2e.", "%2e%2e"})
_HOME_MEMO: Final = 64
_SEGMENT_MEMO: Final = 1024
_SEGMENT_LIMIT: Final = 256


def _canonical(segment: str) -> str:
    """Return one path segment with its percent-encoding normalised.

    A segment of characters no encoding touches comes back as the object passed in.
    """
    if _CANONICAL.fullmatch(segment) is not None:
        return segment
    if len(segment) > _SEGMENT_LIMIT:
        return _recode(segment)
    return _recoded(segment)


def _recode(segment: str) -> str:
    """Return `segment` decoded and encoded again under the one canonical spelling."""
    decoded = unquote(segment, errors="surrogateescape")
    return quote(decoded, safe=_SEGMENT_SAFE, errors="surrogateescape")


_recoded: Final = functools.lru_cache(maxsize=_SEGMENT_MEMO)(_recode)


def normal_path(location: str) -> str:
    """Return an absolute path with dot segments resolved and one trailing slash cut."""
    if _SETTLED.fullmatch(location) is not None:
        return location.removesuffix("/") or "/"
    kept: list[str] = []
    for segment in location.split("/")[1:]:
        folded = segment.lower()
        if folded in _DOTDOT:
            if kept:
                kept.pop()
        elif folded not in _DOT:
            kept.append(_canonical(segment))
    if kept and not kept[-1]:
        kept.pop()
    return "/" + "/".join(kept)


def path_mark(
    link: str,
    current: str,
    homes: Callable[[], Collection[str]],
    *,
    exact: bool = False,
) -> str:
    """Return the marks a link to the normal path `link` gains on the `current` one.

    A home path is exact only like `/`, so `homes` is read for a real ancestor alone.
    """
    if link == current:
        return PAGE_MARK
    if exact or link == "/" or not current.startswith(f"{link}/"):
        return ""
    return "" if link in homes() else ANCESTOR_MARK


@pure_cache(_HOME_MEMO)
def _homes(
    prefix: str, codes: tuple[str, ...], default: str, *, prefixed: bool
) -> frozenset[str]:
    """Return the normal home paths under `prefix`, the unprefixed default skipped."""
    roots = (to_language(code) for code in codes)
    languages = (code for code in roots if prefixed or code != default)
    return frozenset(
        {"/", normal_path(prefix), *(normal_path(f"{prefix}{c}/") for c in languages)}
    )


def home_paths() -> frozenset[str]:
    """Return `/`, the script prefix and each language root `i18n_patterns()` serves."""
    urlconf = get_urlconf() or str(getattr(settings, "ROOT_URLCONF", ""))
    used, prefixed = is_language_prefix_patterns_used(urlconf)
    codes = tuple(code for code, _name in settings.LANGUAGES) if used else ()
    return _homes(get_script_prefix(), codes, settings.LANGUAGE_CODE, prefixed=prefixed)


class Homes:
    """The home paths of one pass over several links, read when a link first asks."""

    __slots__ = ("_held",)

    def __init__(self) -> None:
        """Start before any link asks."""
        self._held: frozenset[str] | None = None

    def __call__(self) -> frozenset[str]:
        """Return the home paths, read from the URLconf and settings only once."""
        held = self._held
        if held is None:
            held = self._held = home_paths()
        return held


__all__ = [
    "ANCESTOR_MARK",
    "PAGE_MARK",
    "Homes",
    "home_paths",
    "normal_path",
    "path_mark",
]
