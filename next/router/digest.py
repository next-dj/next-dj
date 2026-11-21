"""The marker scan of a rendered page and the normalisation of layout chrome.

Pure functions over one response's HTML and seal, so a test reproduces every case.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_left
from functools import partial
from typing import TYPE_CHECKING, Final

from next.pages.layouts import META, SLOT, ZONE, closing, opening
from next.static.runtime import ASSETS_MARK


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence


DIGEST_BYTES: Final = 8
"""The size of one chrome digest, written as 16 hex characters."""

SLOT_STANDIN: Final = "\x00s"
"""What the slot of a layout leaves in its chrome, so its position still counts."""

REGION_STANDIN: Final = "\x00r"
"""What a zone, the head tags or an injected placeholder leave in a chrome."""

_REGION_KINDS: Final = (ZONE, META, ASSETS_MARK)
_QUOTED: Final = re.compile(r"""(?:"[^"]*"|'[^']*')""")
_NONCE_NAME: Final = "nonce="
_CSRF_NAME: Final = re.compile(r"""name=["']csrfmiddlewaretoken["']""", re.IGNORECASE)
_CSRF_HINT: Final = "ddlewareto"
_CSRF_LEAD: Final = len("name='csrfmi")
_CSRF_VALUE: Final = re.compile(r"""(\svalue=)(?:"[^"]*"|'[^']*')""", re.IGNORECASE)
_BODY_OPEN: Final = re.compile(r"<body(?![\w:-])", re.IGNORECASE)
_BODY_CLOSE: Final = re.compile(r"</body\s*>", re.IGNORECASE)
_ZONE_NAME: Final = re.compile(r"""\sdata-next-zone=(?:"([^"]*)"|'([^']*)')""")


def fold(text: str) -> str:
    """Return `text` with ASCII letters lowered and every index kept in place.

    Two code points lower to a longer string or to ASCII, so they are swapped first.
    """
    if text.isascii():
        return text.lower()
    return text.replace("\u0130", "\u0131").replace("\u212a", "\u0138").lower()


def chrome_digest(text: str) -> str:
    """Return the blake2b digest of normalised chrome, as 16 hex characters."""
    return hashlib.blake2b(text.encode(), digest_size=DIGEST_BYTES).hexdigest()


def regions(html: str, kind: str, seal: str) -> list[tuple[int, int]]:
    """Return the outer bounds of the top-level sealed regions of `kind`.

    A region inside another region of the same kind belongs to it.
    """
    return _outermost(_nested(html, kind, seal))


def _nested(html: str, kind: str, seal: str) -> list[tuple[int, int]]:
    """Return the outer bounds of every sealed region of `kind`, ordered by start."""
    closer = closing(kind, seal)
    at_close = html.find(closer)
    if at_close < 0:
        return []
    opener = opening(kind, seal)
    at_open = html.find(opener)
    found: list[tuple[int, int]] = []
    starts: list[int] = []
    while at_close >= 0:
        if 0 <= at_open < at_close:
            starts.append(at_open)
            at_open = html.find(opener, at_open + len(opener))
            continue
        position = at_close + len(closer)
        if starts:
            found.append((starts.pop(), position))
        at_close = html.find(closer, position)
    found.sort()
    return found


def _outermost(
    bounds: Iterable[tuple[int, int]],
    skip: Callable[[tuple[int, int]], bool] | None = None,
) -> list[tuple[int, int]]:
    """Return the ordered `bounds` no kept one encloses, leaving out what `skip` names.

    A left-out region encloses nothing, so the regions inside it count on their own.
    """
    kept: list[tuple[int, int]] = []
    for found in bounds:
        if (kept and found[0] < kept[-1][1]) or (skip is not None and skip(found)):
            continue
        kept.append(found)
    return kept


def _standins(text: str) -> list[int]:
    """Return every position of the slot stand-in in `text`, in order."""
    found: list[int] = []
    at = text.find(SLOT_STANDIN)
    while at >= 0:
        found.append(at)
        at = text.find(SLOT_STANDIN, at + len(SLOT_STANDIN))
    return found


def _holds(points: Sequence[int], bounds: tuple[int, int]) -> bool:
    """Whether one of the ordered `points` falls inside `bounds`."""
    at = bisect_left(points, bounds[0])
    return at < len(points) and points[at] < bounds[1]


def _overlaps(outside: tuple[int, int] | None, bounds: tuple[int, int]) -> bool:
    """Whether `bounds` starts inside `outside` or encloses it."""
    return outside is not None and bounds[0] < outside[1] and outside[0] < bounds[1]


def _without(text: str, bounds: Iterable[tuple[int, int]], standin: str) -> str:
    """Return `text` with each of the ordered, disjoint `bounds` replaced."""
    pieces: list[str] = []
    position = 0
    for start, end in bounds:
        pieces.append(text[position:start])
        pieces.append(standin)
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


def _sealed_regions(text: str, seal: str) -> list[tuple[int, int]]:
    """Return the top-level zone, head and placeholder regions of `text`, in order.

    A zone around the slot is no region, so the chrome it wraps counts as chrome.
    """
    every = sorted(
        bounds for kind in _REGION_KINDS for bounds in _nested(text, kind, seal)
    )
    return _outermost(every, partial(_holds, _standins(text)))


def _without_nonces(text: str, low: str) -> str:
    """Return `text` with each quoted `nonce` attribute and the space before it cut.

    `low` is `text` folded, searched for the name so no pattern walks the whole text.
    """
    at = low.find(_NONCE_NAME)
    if at < 0:
        return text
    pieces: list[str] = []
    position = 0
    while at >= 0:
        value = _QUOTED.match(text, at + len(_NONCE_NAME))
        if value is None or at <= position or not text[at - 1].isspace():
            at = low.find(_NONCE_NAME, at + 1)
            continue
        pieces.append(text[position : at - 1])
        position = value.end()
        at = low.find(_NONCE_NAME, position)
    pieces.append(text[position:])
    return "".join(pieces)


def _masked_csrf(text: str, low: str) -> str:
    """Return `text` with each CSRF token value emptied, a search bound to one tag.

    `low` is `text` folded, and a run of the field name no case rule rewrites finds it.
    """
    hint = low.find(_CSRF_HINT)
    if hint < 0:
        return text
    pieces: list[str] = []
    position = 0
    while hint >= 0:
        start = hint - _CSRF_LEAD
        found = _CSRF_NAME.match(text, start) if start >= position else None
        if found is None:
            hint = low.find(_CSRF_HINT, hint + 1)
            continue
        end = text.find(">", found.end())
        if end < 0:
            break
        pieces.append(text[position : found.end()])
        pieces.append(_CSRF_VALUE.sub(r'\1""', text[found.end() : end], count=1))
        position = end
        hint = low.find(_CSRF_HINT, end)
    pieces.append(text[position:])
    return "".join(pieces)


def normalise(text: str, seal: str) -> str:
    """Return chrome without what changes per response or is re-sent on its own.

    Tokens and boundary ids keep the per-response seal, so deferred chrome never keeps.
    """
    if f" {seal}-->" in text:
        text = _without(text, _sealed_regions(text, seal), REGION_STANDIN)
        text = text.replace(opening(ZONE, seal), "").replace(closing(ZONE, seal), "")
    low = fold(text)
    cut = _without_nonces(text, low)
    return _masked_csrf(cut, low if cut is text else fold(cut))


def _body_start(text: str) -> int | None:
    """Return the index past the `<body>` start tag of `text`, `None` without one."""
    found = _BODY_OPEN.search(text)
    if found is None:
        return None
    end = text.find(">", found.end())
    return None if end < 0 else end + 1


def split_head(text: str) -> tuple[str, str] | None:
    """Return `text` up to the end of its `<body>` start tag and the rest.

    The body tag belongs to the head part, so differing body attributes load hard.
    """
    start = _body_start(text)
    if start is None:
        return None
    return text[:start], text[start:]


def body_bounds(html: str) -> tuple[int, int] | None:
    """Return the bounds of the children of the `<body>` of `html`, `None` for none."""
    start = _body_start(html)
    if start is None:
        return None
    ends = list(_BODY_CLOSE.finditer(html, start))
    end = ends[-1].start() if ends else len(html)
    return start, end


def find_slot(
    html: str, ident: str, seal: str, within: tuple[int, int]
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Return the inner and outer bounds of the slot of `ident` inside `within`.

    The comments carry the response seal, so a look-alike in content never matches.
    """
    opener = opening(SLOT, seal, ident)
    closer = closing(SLOT, seal, ident)
    start, stop = within
    at = html.find(opener, start, stop)
    if at < 0:
        return None
    inner = at + len(opener)
    end = html.rfind(closer, inner, stop)
    if end < 0:
        return None
    return (inner, end), (at, end + len(closer))


def chrome(html: str, region: tuple[int, int], slot: tuple[int, int]) -> str:
    """Return the text of `region` with the outer bounds `slot` of its slot left out."""
    start, end = region
    return html[start : slot[0]] + SLOT_STANDIN + html[slot[1] : end]


def zone_name(html: str, bounds: tuple[int, int], seal: str) -> str | None:
    """Return the name the first element of a sealed zone region carries."""
    start = bounds[0] + len(opening(ZONE, seal))
    tag_end = html.find(">", start, bounds[1])
    found = _ZONE_NAME.search(html, start, tag_end if tag_end >= 0 else bounds[1])
    if found is None:
        return None
    return found.group(1) if found.group(1) is not None else found.group(2)


def zone_html(html: str, bounds: tuple[int, int], seal: str) -> str:
    """Return a sealed zone region without its two comments."""
    start = bounds[0] + len(opening(ZONE, seal))
    end = bounds[1] - len(closing(ZONE, seal))
    return html[start:end]


def chrome_zones(
    html: str, seal: str, outside: tuple[int, int] | None
) -> Iterator[tuple[str, tuple[int, int]]]:
    """Yield the name and bounds of each top-level zone outside the bounds `outside`.

    A zone around `outside` is left out for the zones inside it, as its chrome is kept.
    """
    for bounds in _outermost(_nested(html, ZONE, seal), partial(_overlaps, outside)):
        name = zone_name(html, bounds, seal)
        if name is not None:
            yield name, bounds


__all__ = [
    "DIGEST_BYTES",
    "REGION_STANDIN",
    "SLOT_STANDIN",
    "body_bounds",
    "chrome",
    "chrome_digest",
    "chrome_zones",
    "find_slot",
    "fold",
    "normalise",
    "regions",
    "split_head",
    "zone_html",
    "zone_name",
]
