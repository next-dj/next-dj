"""The inline delivery of a session, every settled job spliced into its wrapper."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Final

from next.diagnostics import mark_degraded
from next.suspense.markers import Outcome
from next.suspense.render import Delta
from next.suspense.response.markup import STATE, closed, openings
from next.suspense.response.stream import failure_log
from next.suspense.session import ID_HEAD, IDENT, BoundarySession


if TYPE_CHECKING:
    from next.static.collector import StaticCollector
    from next.suspense.markers import Rendered


_STALE: Final = re.compile(
    rf"<!--(?P<id>{IDENT.pattern}):[A-Za-z0-9-]*-->"
    r'<[A-Za-z][^>]* id="(?P=id)" data-next-boundary="(?P=id)"'
)
_CAPTURED: Final = (
    "The response holds the loading wrapper of a deferred boundary from an earlier "
    "response, so a tag that stores rendered output, such as {% cache %}, captured "
    "it. Render the component inline there, defer='inline' at the call site."
)


type Run = Callable[[BoundarySession[Delta]], Iterable[Iterable[Outcome[Delta]]]]
"""What runs the jobs of a session and yields their outcomes in batches."""


class _Assembly:
    """The outcomes of one response and what splicing them found.

    Only an opening marker of this response naming a queued job splices, once, so a
    marker an echo or an older response wrote stays the text it is.
    """

    __slots__ = ("collector", "degraded", "intended", "outcomes", "seal")

    def __init__(
        self,
        outcomes: dict[str, Outcome[Delta]],
        collector: StaticCollector | None,
        seal: str,
    ) -> None:
        """Hold the outcomes by id, nothing found yet."""
        self.outcomes = outcomes
        self.collector = collector
        self.seal = seal
        self.degraded = False
        self.intended: Exception | None = None

    def splice(self, text: str) -> str:
        """Return `text` with every loading wrapper replaced by its settled one."""
        parts: list[str] = []
        position = 0
        for start, opened, ident, tag in openings(text, self.seal):
            if start < position:
                continue
            closing = f"<!--/{ident}-->"
            end = text.find(closing, opened)
            outcome = self.outcomes.pop(ident, None)
            if end < 0 or outcome is None:
                continue
            parts.append(text[position:start])
            segment = text[opened:end]
            parts.append(self._settle(tag, ident, segment, outcome))
            position = end + len(closing)
        parts.append(text[position:])
        return "".join(parts)

    def _settle(
        self, tag: str | None, ident: str, segment: str, outcome: Outcome[Delta]
    ) -> str:
        """Return the settled wrapper of one outcome, its assets merged first."""
        rendered = outcome.rendered
        self._take(rendered)
        start = segment.index(f' id="{ident}"')
        attributes = segment[start : segment.index(STATE, start)]
        body = self.splice(rendered.html)
        return closed(
            tag, attributes, body, rendered.kind, rendered.failure, rendered.retry
        )

    def _take(self, rendered: Rendered[Delta]) -> None:
        """Merge the assets of one outcome and note how it settled, in order."""
        if self.collector is not None:
            rendered.delta.apply(self.collector)
        if rendered.status is not None:
            mark_degraded(rendered.status)
        if rendered.degraded or rendered.kind is not None:
            self.degraded = True
        if self.intended is None:
            self.intended = rendered.intended


def _stale(shell_html: str, seal: str) -> None:
    """Warn when the shell holds a loading wrapper another response sealed."""
    own = f"{ID_HEAD}{seal}-"
    if any(not found["id"].startswith(own) for found in _STALE.finditer(shell_html)):
        failure_log.warn(("captured",), _CAPTURED)


def assemble(
    shell_html: str,
    outcomes: Iterable[Outcome[Delta]],
    collector: StaticCollector | None,
    *,
    seal: str,
) -> str:
    """Return the shell with every outcome in its wrapper, nested ones inside parents.

    The assets merge before injection, and an intended exception of a job raises here.
    """
    _stale(shell_html, seal)
    held = {outcome.id: outcome for outcome in outcomes}
    assembly = _Assembly(held, collector, seal)
    html = assembly.splice(shell_html)
    if assembly.intended is not None:
        raise assembly.intended
    if assembly.degraded:
        mark_degraded()
    return html


def prune(session: BoundarySession[Delta], shell_html: str) -> BoundarySession[Delta]:
    """Return the session without the jobs whose wrapper the shell no longer holds.

    A failed subtree discards its markup, so a job queued inside it never runs.
    """
    present = {ident for _, _, ident, _ in openings(shell_html, session.seal)}
    kept = [job for job in session.jobs if job.id in present]
    if len(kept) == len(session.jobs):
        return session
    return session.narrowed(kept)


def finish(
    shell_html: str,
    session: BoundarySession[Delta],
    collector: StaticCollector | None,
    run: Run,
) -> str:
    """Drop the jobs the shell lost, run the rest through `run`, assemble the page."""
    live = prune(session, shell_html)
    outcomes = [outcome for batch in run(live) for outcome in batch]
    return assemble(shell_html, outcomes, collector, seal=session.seal)


__all__ = ["Run", "assemble", "finish", "prune"]
