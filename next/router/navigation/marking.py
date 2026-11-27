"""The active-link passes over a page, its containers, its zones and its actions."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Final

from django.template import Template

from next.forms.uid import posted_origin_path
from next.marks import fragment_marks
from next.partial.headers import ORIGIN, vary_on
from next.router.links import TemplateLinks, active_links, origin_of
from next.static.runtime import html_finish

from .intent import origin_path
from .shell import NOTHING


if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from types import TracebackType

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase

    from next.marks import FixedText


_ORIGIN_VARY: Final = (ORIGIN,)

type Compiler = Callable[[Template, TemplateLinks], bool]
"""What wraps the containers of a template, true once it shows every one."""


def marked_links(
    html: str, request: HttpRequest, hidden: tuple[int, int] = NOTHING
) -> str:
    """Return `html` with its current links marked, once its digests are taken.

    `hidden` spans the `$router` entry, whose selectors open no container.
    """
    links = active_links()
    if links is None:
        return html
    path, origin = request.get_full_path, partial(origin_of, request)
    return links.mark(html, path, origin, hidden=hidden)


class FragmentMarks:
    """The active-link pass over the output of one container or component.

    The backend and the origin reader are taken once per page, as every fragment reads.
    """

    __slots__ = ("_compiler", "_links", "_origin", "_path")

    def __init__(
        self, links: TemplateLinks, request: HttpRequest, compiler: Compiler
    ) -> None:
        """Hold the backend, what it reads of `request` and the container compiler."""
        self._links = links
        self._compiler = compiler
        self._path = request.get_full_path
        self._origin = partial(origin_of, request)

    def __call__(self, html: str) -> str:
        """Return `html` with its current links marked, itself when it holds none."""
        return self._links.mark(html, self._path, self._origin)

    def compiled(self, template: Template) -> bool:
        """Whether every container `template` writes marks its own links."""
        return self._compiler(template, self._links)

    def fixed(self, text: FixedText) -> str:
        """Return the text of a fixed container with its current links marked."""
        return text.mark(self._path, self._origin)


def fragment_pass(request: HttpRequest, compiler: Compiler) -> FragmentMarks | None:
    """Return the pass each fragment of a page runs, `None` while links are off."""
    links = active_links()
    return None if links is None else FragmentMarks(links, request, compiler)


class _Marking:
    """The block that binds one pass over the HTML rendered inside it."""

    __slots__ = ("_marks", "_token")

    def __init__(self, marks: Callable[[str], str] | None) -> None:
        """Hold the pass to bind, `None` to bind none."""
        self._marks = marks

    def __enter__(self) -> None:
        """Bind the pass as the one the render inside runs over its HTML."""
        self._token = html_finish.set(self._marks)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Unbind the pass, whatever the render inside it did."""
        html_finish.reset(self._token)


def page_marking(request: HttpRequest) -> AbstractContextManager[None]:
    """Mark the current links of the page the block renders for `request`."""
    return _Marking(partial(marked_links, request=request))


class _Fragments:
    """The block that binds the active-link pass each container of a page runs."""

    __slots__ = ("_compiler", "_request", "_token")

    def __init__(self, request: HttpRequest, compiler: Compiler) -> None:
        """Hold the request the links are marked for and the container compiler."""
        self._request = request
        self._compiler = compiler

    def __enter__(self) -> None:
        """Bind the pass the containers rendered inside run over their own HTML."""
        self._token = fragment_marks.set(fragment_pass(self._request, self._compiler))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Unbind the pass, whatever the render inside did."""
        fragment_marks.reset(self._token)


def fragment_marking(
    request: HttpRequest, compiler: Compiler
) -> AbstractContextManager[None]:
    """Mark the links of each container the page renders, as each one renders.

    `compiler` wraps the containers of a component template the page renders.
    """
    return _Fragments(request, compiler)


class _ZoneMarks:
    """The active-link pass over the zone HTML of one request.

    The path and the origin are read once a zone opens a navigation container.
    """

    __slots__ = ("_links", "_origin", "_path", "_request")

    def __init__(
        self, links: TemplateLinks, request: HttpRequest, path: str | None
    ) -> None:
        """Hold the backend and the request, with `path` when the caller names one."""
        self._links = links
        self._request = request
        self._path = path
        self._origin: str | None = None

    def __call__(self, html: str) -> str:
        """Return `html` marked for the page the client shows, itself when unmarked."""
        return self._links.mark(html, self.path, self.origin)

    def path(self) -> str:
        """Return the path of the page the client shows, read once a zone needs it."""
        path = self._path
        if path is None:
            request = self._request
            path = self._path = origin_path(request) or request.get_full_path()
        return path

    def origin(self) -> str:
        """Return the scheme and host of the request, read once a zone needs them."""
        origin = self._origin
        if origin is None:
            origin = self._origin = origin_of(self._request)
        return origin


def zone_marking(request: HttpRequest) -> AbstractContextManager[None]:
    """Mark the zone HTML the block renders for the page the client shows.

    A layer and the zones inside it name the page beneath in `X-Next-Origin`.
    """
    links = active_links()
    return _Marking(None if links is None else _ZoneMarks(links, request, None))


def action_marking(request: HttpRequest) -> AbstractContextManager[None]:
    """Mark the zone HTML an action response renders for the page its form lives on."""
    links = active_links()
    if links is None:
        return _Marking(None)
    path = origin_path(request) or posted_origin_path(request)
    return _Marking(None if path is None else _ZoneMarks(links, request, path))


def origin_vary[R: HttpResponseBase](response: R) -> R:
    """Vary zone `response` on `X-Next-Origin` while the active-link pass marks it."""
    if active_links() is not None:
        vary_on(response, _ORIGIN_VARY)
    return response


__all__ = [
    "Compiler",
    "FragmentMarks",
    "action_marking",
    "fragment_marking",
    "fragment_pass",
    "marked_links",
    "origin_vary",
    "page_marking",
    "zone_marking",
]
