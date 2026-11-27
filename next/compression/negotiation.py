"""The `Accept-Encoding` parse and the choice of a content coding in server order."""

import re
from typing import Final

from next.caches import pure_cache


MAX_HEADER: Final = 256
"""The most characters of `Accept-Encoding` the parse reads."""

_CODING: Final = re.compile(r"[!#$%&'*+.^_`|~0-9a-z-]+")
_QVALUE: Final = re.compile(r"0(?:\.\d{0,3})?|1(?:\.0{0,3})?")
_ALIASES: Final = {"x-gzip": "gzip"}
_ANY: Final = "*"


def _trimmed(header: str) -> str:
    """Return the part of `header` the parse reads, minus an element the cut splits."""
    if len(header) <= MAX_HEADER:
        return header
    return header[:MAX_HEADER].rpartition(",")[0]


def _weight(params: list[str]) -> float | None:
    """Return the `q` of one element, `1.0` without one, `None` when it is malformed."""
    weight = 1.0
    for param in params:
        name, sep, value = param.partition("=")
        if name.strip().lower() != "q":
            continue
        value = value.strip()
        if not sep or _QVALUE.fullmatch(value) is None:
            return None
        weight = float(value)
    return weight


@pure_cache(256)
def _parse(header: str) -> tuple[tuple[str, float], ...]:
    """Return each coding `header` names with its weight, the first mention winning."""
    seen: dict[str, float] = {}
    for element in header.split(","):
        coding, *params = element.split(";")
        coding = coding.strip().lower()
        if _CODING.fullmatch(coding) is None:
            continue
        weight = _weight(params)
        if weight is not None:
            seen.setdefault(_ALIASES.get(coding, coding), weight)
    return tuple(seen.items())


def parse_accept_encoding(header: str) -> tuple[tuple[str, float], ...]:
    """Return the codings of an `Accept-Encoding` value with weights, never raising.

    A malformed element is skipped and only the first `MAX_HEADER` characters are read.
    """
    return _parse(_trimmed(header))


@pure_cache(512)
def _choose(header: str, offered: tuple[str, ...]) -> str | None:
    """Return the offered coding weighed highest, ties going to the earlier one."""
    weights = dict(_parse(header))
    fallback = weights.get(_ANY, 0.0)
    best: str | None = None
    best_weight = 0.0
    for coding in offered:
        weight = weights.get(coding, fallback)
        if weight > best_weight:
            best, best_weight = coding, weight
    return best


def negotiate(header: str, offered: tuple[str, ...]) -> str | None:
    """Return the coding of `offered` to send for `header`, `None` for identity.

    `q=0` excludes a coding, `*` weighs the unnamed ones, an empty header is identity.
    """
    if not header or not offered:
        return None
    return _choose(_trimmed(header), offered)


__all__ = ["MAX_HEADER", "negotiate", "parse_accept_encoding"]
