"""The client router state of one page view, bound around its render."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from next.conf.router import router_enabled
from next.marks import fragment_marks
from next.pages.layouts import mint_response_id
from next.pages.loaders import layout_ids
from next.router.backends import ChromeDigest
from next.router.declarations import page_router
from next.router.manager import client_router
from next.router.render import RouterRender, router_render
from next.static.runtime import ROUTER_PAYLOAD_KEY

from .marking import fragment_pass, marked_links, page_marking
from .shell import NONE_FIXED, NOTHING, layout_entries, relocated, scanned_layouts


if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextlib import AbstractContextManager
    from contextvars import Token
    from pathlib import Path
    from types import TracebackType

    from django.http import HttpRequest

    from next.marks import FragmentPass
    from next.requests import Purpose
    from next.router.markers import ScannedLayout, ScannedPage

    from .marking import Compiler


_ROUTER_ENTRY: Final = f"{json.dumps(ROUTER_PAYLOAD_KEY)}:"


class Routing:
    """The client router state of one page view, from its render to its response.

    A document reads only the digests, and the bounds a navigation cuts by come later.
    """

    __slots__ = (
        "file_path",
        "fixed",
        "fragments",
        "html",
        "layouts",
        "purpose",
        "render",
        "request",
        "shell",
    )

    def __init__(
        self,
        request: HttpRequest,
        file_path: Path,
        purpose: Purpose,
        *,
        fragments: Compiler | None = None,
        fixed: Mapping[str, tuple[str, str | None]] = NONE_FIXED,
    ) -> None:
        """Mint the response id and the state the injector reads.

        `fragments` compiles the containers of a page that marks its own links as it
        renders, and `fixed` holds the digests fixed chrome took from its source.
        """
        self.request = request
        self.fragments = fragments
        self.fixed = fixed
        self.file_path = file_path
        self.purpose = purpose
        entry, speculation = client_router.document()
        self.render = RouterRender(
            mint_response_id(request),
            entry,
            self.finish,
            snapshot=page_router(file_path).snapshot,
            speculation=speculation,
        )
        self.layouts: tuple[ScannedLayout, ...] = ()
        self.shell: ScannedPage | None = None
        self.html: str | None = None

    @property
    def response_id(self) -> str:
        """Return the response id every marker of the response carries."""
        return self.render.response_id

    def finish(self, html: str) -> str:
        """Digest the layouts of `html`, fill their block and run the passes after."""
        response_id = self.response_id
        backend = client_router.digest()
        fixed = self.fixed if type(backend) is ChromeDigest else NONE_FIXED
        self.layouts = layouts = scanned_layouts(
            html, response_id, layout_ids(self.file_path), backend, fixed
        )
        mark = self.render.mark
        # The layouts block closes the page, so the search starts from its end.
        at = html.rfind(mark)
        hidden = NOTHING
        if at >= 0:
            entries = layout_entries(layouts, response_id)
            payload = json.dumps(entries, separators=(",", ":"))
            html = f"{html[:at]}{payload}{html[at + len(mark) :]}"
            hidden = (html.rfind(_ROUTER_ENTRY, 0, at), at)
        if not self.fragments:
            html = marked_links(html, self.request, hidden)
        self.html = html
        self.shell = None
        return html

    def settled(self, html: str) -> ScannedPage:
        """Return the shell of `html`, finished here when the injector never did."""
        if self.html is not html:
            html = self.finish(html)
        shell = self.shell
        if shell is None:
            shell = self.shell = relocated(self.layouts, self.response_id, html)
        return shell


class _Routed:
    """The block that binds the router state of one page view to the render inside."""

    __slots__ = ("_held", "_marks", "_token")

    def __init__(self, held: Routing) -> None:
        """Hold the state to bind."""
        self._held = held
        self._marks: Token[FragmentPass | None] | None = None

    def __enter__(self) -> Routing:
        """Bind the state the injector reads, and the pass its containers run."""
        held = self._held
        self._token = router_render.set(held.render)
        if held.fragments is not None:
            marks = fragment_pass(held.request, held.fragments)
            self._marks = fragment_marks.set(marks)
        return held

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Unbind the state, whatever the render inside did."""
        if self._marks is not None:
            fragment_marks.reset(self._marks)
        router_render.reset(self._token)


def routing(
    request: HttpRequest,
    file_path: Path,
    purpose: Purpose,
    *,
    fragments: Compiler | None = None,
    fixed: Mapping[str, tuple[str, str | None]] = NONE_FIXED,
) -> AbstractContextManager[Routing | None]:
    """Render the block under the client router, yielding `None` while it is off.

    With the router off the injector still runs the active-link pass over the page.
    """
    if not router_enabled():
        return page_marking(request)
    return _Routed(
        Routing(request, file_path, purpose, fragments=fragments, fixed=fixed)
    )


__all__ = ["Routing", "routing"]
