"""System check for a `ROUTER["EXCLUDE"]` prefix that no route serves."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning
from django.urls import (
    LocalePrefixPattern,
    URLPattern,
    URLResolver,
    get_resolver,
    get_script_prefix,
)

from next.checks import NEXT, register
from next.conf.router import router_enabled, router_settings
from next.router.manager import asset_prefixes

from .settings import PREFIX


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


_REGEX_META: Final = frozenset(".^$*+?{}[]()|")
_QUANTIFIERS: Final = frozenset("?*{")
_ENDS: Final = frozenset({"$", r"\Z"})


def _regex_head(regex: str) -> tuple[str, str]:
    """Return the literal start of a URL pattern regex and the rest after it."""
    if "|" in regex:
        return "", regex
    text = regex.removeprefix("^")
    head: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        escaped = char == "\\" and not text[index + 1 : index + 2].isalnum()
        if char in _REGEX_META or (char == "\\" and not escaped):
            break
        step = 2 if escaped else 1
        if text[index + step : index + step + 1] in _QUANTIFIERS:
            break
        head.append(text[index + step - 1])
        index += step
    return "".join(head), text[index:]


def _pattern_heads(entry: URLPattern | URLResolver) -> Iterator[tuple[str, bool]]:
    """Yield each literal start of an entry and whether the entry is that literal.

    A language prefix stands for every configured language and for none.
    """
    pattern = entry.pattern
    if isinstance(pattern, LocalePrefixPattern):
        yield "", True
        for code, _name in settings.LANGUAGES:
            yield f"{code}/", True
        return
    head, rest = _regex_head(pattern.regex.pattern)
    yield head, rest in _ENDS if isinstance(entry, URLPattern) else not rest


def _route_heads(
    entries: Iterable[URLPattern | URLResolver], above: str = ""
) -> Iterator[tuple[str, bool]]:
    """Yield the literal start of every route and whether a path may extend it."""
    for entry in entries:
        for head, whole in _pattern_heads(entry):
            if isinstance(entry, URLResolver) and whole:
                yield from _route_heads(entry.url_patterns, above + head)
            else:
                yield above + head, not whole


def _serves_under(rest: str, heads: Iterable[tuple[str, bool]]) -> bool:
    """Return whether some route may serve a path that starts with `rest`."""
    return any(
        head.startswith(rest) or (extends and rest.startswith(head))
        for head, extends in heads
    )


@register(Tags.urls, NEXT)
def check_exclude_prefixes(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a `ROUTER["EXCLUDE"]` prefix no route serves (`next.W184`).

    A converter or a regex group past the literal start of a route serves anything.
    """
    written = router_settings().exclude
    if not router_enabled() or not written:
        return []
    script = get_script_prefix()
    assets = asset_prefixes()
    heads = list(_route_heads(get_resolver().url_patterns))
    return [
        DjangoWarning(
            f"{PREFIX}['EXCLUDE'] holds {prefix!r}, which starts no path the URLconf "
            "serves, so it keeps nothing out of the client router.",
            hint="Correct the prefix to the path of a route, or drop it. Silence "
            "next.W184 for a prefix that a server outside Django answers.",
            obj=settings,
            id="next.W184",
        )
        for prefix in written
        if prefix.startswith(script)
        and not any(prefix.startswith(a) or a.startswith(prefix) for a in assets)
        and not _serves_under(prefix.removeprefix(script), heads)
    ]


__all__ = ["check_exclude_prefixes"]
