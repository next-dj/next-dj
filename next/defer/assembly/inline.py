"""The inline delivery of a session, every settled job spliced into its wrapper."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Final

from next.boundaries.ids import ID_HEAD, IDENT
from next.defer.ledger import ResponseJobs
from next.defer.markers import Piece
from next.defer.response.chunks import failure_log
from next.defer.response.markup import STATE, closed, openings
from next.diagnostics import mark_degraded
from next.partial.keys import BOUNDARY_ATTR


if TYPE_CHECKING:
    from next.defer.markers import Rendered
    from next.static.collector import StaticCollector


_STALE: Final = re.compile(
    rf"<!--(?P<id>{IDENT.pattern}):[A-Za-z0-9-]*-->"
    rf'<[A-Za-z][^>]* id="(?P=id)" {BOUNDARY_ATTR}="(?P=id)"'
)
_CAPTURED: Final = (
    "The response holds the loading wrapper of a deferred boundary from an earlier "
    "response, so a tag that stores rendered output, such as {% cache %}, captured "
    "it. Render the component inline there, defer='inline' at the call site."
)


type Run = Callable[[ResponseJobs], Iterable[Iterable[Piece]]]
"""What runs the jobs of a session and yields their pieces in batches."""


class _Assembly:
    """The pieces of one response and what splicing them found.

    Only an opening marker of this response naming a queued job splices, once, so a
    marker an echo or an older response wrote stays the text it is.
    """

    __slots__ = ("collector", "degraded", "intended", "pieces", "response_id")

    def __init__(
        self,
        pieces: dict[str, Piece],
        collector: StaticCollector | None,
        response_id: str,
    ) -> None:
        """Hold the pieces by id, nothing found yet."""
        self.pieces = pieces
        self.collector = collector
        self.response_id = response_id
        self.degraded = False
        self.intended: Exception | None = None

    def splice(self, text: str) -> str:
        """Return `text` with every loading wrapper replaced by its settled one."""
        parts: list[str] = []
        position = 0
        for start, opened, ident, tag in openings(text, self.response_id):
            if start < position:
                continue
            closing = f"<!--/{ident}-->"
            end = text.find(closing, opened)
            piece = self.pieces.pop(ident, None)
            if end < 0 or piece is None:
                continue
            parts.append(text[position:start])
            segment = text[opened:end]
            parts.append(self._settle(tag, ident, segment, piece))
            position = end + len(closing)
        parts.append(text[position:])
        return "".join(parts)

    def _settle(self, tag: str | None, ident: str, segment: str, piece: Piece) -> str:
        """Return the settled wrapper of one piece, its assets merged first."""
        rendered = piece.rendered
        self._take(rendered)
        start = segment.index(f' id="{ident}"')
        attributes = segment[start : segment.index(STATE, start)]
        body = self.splice(rendered.html)
        return closed(
            tag, attributes, body, rendered.kind, rendered.failure, rendered.retry
        )

    def _take(self, rendered: Rendered) -> None:
        """Merge the assets of one piece and note how it settled, in order."""
        if self.collector is not None:
            rendered.delta.apply(self.collector)
        if rendered.status is not None:
            mark_degraded(rendered.status)
        if rendered.degraded or rendered.kind is not None:
            self.degraded = True
        if self.intended is None:
            self.intended = rendered.intended


def _stale(shell_html: str, response_id: str) -> None:
    """Warn when the shell holds a loading wrapper another response minted."""
    own = f"{ID_HEAD}{response_id}-"
    if any(not found["id"].startswith(own) for found in _STALE.finditer(shell_html)):
        failure_log.warn(("captured",), _CAPTURED)


def assemble(
    shell_html: str,
    pieces: Iterable[Piece],
    collector: StaticCollector | None,
    *,
    response_id: str,
) -> str:
    """Return the shell with every piece in its wrapper, nested ones inside parents.

    The assets merge before injection, and an intended exception of a job raises here.
    """
    _stale(shell_html, response_id)
    held = {piece.id: piece for piece in pieces}
    assembly = _Assembly(held, collector, response_id)
    html = assembly.splice(shell_html)
    if assembly.intended is not None:
        raise assembly.intended
    if assembly.degraded:
        mark_degraded()
    return html


def prune(session: ResponseJobs, shell_html: str) -> ResponseJobs:
    """Return the session without the jobs whose wrapper the shell no longer holds.

    A failed subtree discards its markup, so a job queued inside it never runs.
    """
    present = {ident for _, _, ident, _ in openings(shell_html, session.response_id)}
    kept = [job for job in session.jobs if job.id in present]
    if len(kept) == len(session.jobs):
        return session
    return session.narrowed(kept)


def finish(
    shell_html: str, session: ResponseJobs, collector: StaticCollector | None, run: Run
) -> str:
    """Drop the jobs the shell lost, run the rest through `run`, assemble the page."""
    live = prune(session, shell_html)
    pieces = [piece for batch in run(live) for piece in batch]
    return assemble(shell_html, pieces, collector, response_id=session.response_id)


__all__ = ["Run", "assemble", "finish", "prune"]
