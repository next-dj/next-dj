"""The kinds of request a next.dj view tells apart, one vocabulary for every layer.

The view routes by it, the transaction rule receives it, and logs spell it the same.
"""

import enum


class RequestKind(enum.StrEnum):
    """What a request to a next.dj view asks for."""

    PAGE = "page"
    """A full page, a browser document or speculation."""

    ACTION = "action"
    """A form action dispatch."""

    ZONE = "zone"
    """The zones of a page a partial request names."""

    BOUNDARY = "boundary"
    """One boundary a token reloads."""

    NAVIGATE = "navigate"
    """A client router navigation or prefetch."""


class PrefetchDepth(enum.StrEnum):
    """How much of a page a router prefetch asks for, which `request.next.depth` names.

    A shell runs no deferred job, and a full prefetch is the page a press may show.
    """

    SHELL = "shell"
    FULL = "full"


__all__ = ["PrefetchDepth", "RequestKind"]
