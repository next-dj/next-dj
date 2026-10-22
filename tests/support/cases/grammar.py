from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InterceptNameCase:
    """One directory name and the marker and segment it parses to, or `None`."""

    name: str
    parsed: tuple[str, str] | None


INTERCEPT_NAME_CASES: tuple[InterceptNameCase, ...] = (
    InterceptNameCase("(.)x", ("(.)", "x")),
    InterceptNameCase("(..)x", ("(..)", "x")),
    InterceptNameCase("(..)(..)x", ("(..)(..)", "x")),
    InterceptNameCase("(..)(..)(..)x", ("(..)(..)(..)", "x")),
    InterceptNameCase("(...)x", ("(...)", "x")),
    InterceptNameCase("(.)[id]", ("(.)", "[id]")),
    InterceptNameCase("(..)[int:id]", ("(..)", "[int:id]")),
    InterceptNameCase("(.)", None),
    InterceptNameCase("(..)", None),
    InterceptNameCase("(...)", None),
    InterceptNameCase("(.)(..)x", None),
    InterceptNameCase("(..)(.)x", None),
    InterceptNameCase("(..)(...)x", None),
    InterceptNameCase("(....)x", None),
    InterceptNameCase("(.)x/y", None),
    InterceptNameCase("(.) x", None),
    InterceptNameCase("(.)(x)", None),
    InterceptNameCase("(.)x(y)", None),
    InterceptNameCase("(app)", None),
    InterceptNameCase("(x)(.)y", None),
    InterceptNameCase("photo", None),
    InterceptNameCase("[id]", None),
    InterceptNameCase(".hidden", None),
    InterceptNameCase("", None),
)


@dataclass(frozen=True, slots=True)
class InterceptResolveCase:
    """One scope, marker and segment, and the URL trail the intercept targets."""

    scope: str
    marker: str
    rest: str
    target: str | None


INTERCEPT_RESOLVE_CASES: tuple[InterceptResolveCase, ...] = (
    InterceptResolveCase("", "(.)", "photo", "photo"),
    InterceptResolveCase("feed", "(.)", "photo", "feed/photo"),
    InterceptResolveCase("feed/[int:id]", "(.)", "[slug]", "feed/[int:id]/[slug]"),
    InterceptResolveCase("a", "(..)", "x", "x"),
    InterceptResolveCase("a/b", "(..)", "x", "a/x"),
    InterceptResolveCase("a/b/c", "(..)", "x", "a/b/x"),
    InterceptResolveCase("a/b", "(..)(..)", "x", "x"),
    InterceptResolveCase("a/b/c", "(..)(..)", "x", "a/x"),
    InterceptResolveCase("", "(..)", "x", None),
    InterceptResolveCase("a", "(..)(..)", "x", None),
    InterceptResolveCase("a/b", "(..)(..)(..)", "x", None),
    InterceptResolveCase("", "(...)", "x", "x"),
    InterceptResolveCase("a/b/c", "(...)", "x", "x"),
    InterceptResolveCase("a/b/c", "(...)", "[id]", "[id]"),
)
