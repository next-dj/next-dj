from __future__ import annotations

import json
from dataclasses import dataclass

from django.utils.http import urlsafe_base64_encode


@dataclass(frozen=True, slots=True)
class NumberCase:
    """One query string over 95 rows of 10 a page and the window it reads."""

    id: str
    query: str
    rows: range
    next: str | None
    previous: str | None
    backs: tuple[str, ...] = ()


NUMBER_CASES: tuple[NumberCase, ...] = (
    NumberCase("no-page", "", range(10), "2", None),
    NumberCase("plain", "page=3", range(20, 30), "4", "2"),
    NumberCase("leading-zeros", "page=007", range(60, 70), "8", "6"),
    NumberCase("zero", "page=0", range(10), "2", None),
    NumberCase("negative", "page=-2", range(10), "2", None),
    NumberCase("plus-sign", "page=%2B4", range(10), "2", None),
    NumberCase("text", "page=abc", range(10), "2", None),
    NumberCase("empty", "page=", range(10), "2", None),
    NumberCase("decimal", "page=2.5", range(10), "2", None),
    NumberCase("non-ascii-digit", "page=%D9%A3", range(10), "2", None),
    NumberCase("past-the-ceiling", f"page={'9' * 19}", range(10), "2", None),
    NumberCase("too-many-digits", f"page={'1' * 20}", range(10), "2", None),
    NumberCase("last", "page=10", range(90, 95), None, "9"),
    NumberCase("past-the-end", "page=11", range(0), None, None),
    NumberCase("addressable-but-empty", f"page={10**17}", range(0), None, None),
    NumberCase(
        "restore-short", "page=5&restore=1", range(50), "6", None, ("1", "2", "3", "4")
    ),
    NumberCase(
        "restore-bounded",
        "page=12&restore=1",
        range(20, 95),
        None,
        "2",
        ("3", "4", "5", "6", "7", "8", "9"),
    ),
    NumberCase(
        "restore-whole",
        "page=10&restore=1",
        range(95),
        None,
        None,
        ("1", "2", "3", "4", "5", "6", "7", "8", "9"),
    ),
    NumberCase("restore-far", "page=25&restore=1", range(0), None, None),
    NumberCase("restore-other-value", "page=3&restore=true", range(20, 30), "4", "2"),
    NumberCase("restore-without-page", "restore=1", range(10), "2", None),
)


def raw_token(payload: object) -> str:
    """Return the token a keyset pager would read the JSON `payload` from."""
    return urlsafe_base64_encode(json.dumps(payload).encode())


@dataclass(frozen=True, slots=True)
class TokenCase:
    """One token a keyset pager ordered by name and pk refuses for the first window."""

    id: str
    token: str


MALFORMED_TOKENS: tuple[TokenCase, ...] = (
    TokenCase("not-a-slug", "a b"),
    TokenCase("non-ascii", "Wé"),
    TokenCase("too-long", "W" * 513),
    TokenCase("bad-base64", "A"),
    TokenCase("not-json", urlsafe_base64_encode(b"nope")),
    TokenCase("not-utf8", urlsafe_base64_encode(b"\x80\x81")),
    TokenCase("object", raw_token({"a": 1})),
    TokenCase("scalar", raw_token(5)),
    TokenCase("empty-list", raw_token([])),
    TokenCase("direction-not-bool", raw_token([1, "g001", 1])),
    TokenCase("nested", raw_token([False, ["g001"], 1])),
    TokenCase("null", raw_token([False, None, 1])),
    TokenCase("nan", urlsafe_base64_encode(b'[false,"g001",NaN]')),
    TokenCase("infinite", urlsafe_base64_encode(b'[false,"g001",1e999]')),
    TokenCase("too-few", raw_token([False, "g001"])),
    TokenCase("too-many", raw_token([False, "g001", 1, 2])),
    TokenCase("bad-int", raw_token([False, "g001", "abc"])),
    TokenCase("int-out-of-range", raw_token([False, "g001", 2**70])),
    TokenCase("nul-character", raw_token([False, "g\x00", 1])),
    TokenCase("too-long-for-the-column", raw_token([False, "g" * 200, 1])),
)
