from dataclasses import dataclass

from next.defer import BoundaryOptions, ErrorKind
from next.suspense.markers import Job, Rendered, Site
from tests.support.boundaries import make_site


PLAIN_SITE = make_site("row", origin="list.djx")


@dataclass(frozen=True, slots=True)
class Ready:
    """A job body rendering fixed markup, equal to another that renders the same."""

    html: str
    delta: str

    def __call__(self) -> Rendered[str]:
        return Rendered(self.html, self.delta)


@dataclass(frozen=True, slots=True)
class ReadyLater:
    """The coroutine body of the same markup."""

    html: str
    delta: str

    async def __call__(self) -> Rendered[str]:
        return Rendered(self.html, self.delta)


@dataclass(frozen=True, slots=True)
class Failed:
    """The error UI of a job, the same for every kind."""

    def __call__(self, kind: ErrorKind) -> Rendered[str]:
        return Rendered("<p>failed</p>", "", kind=kind)


def plain_job(
    ident: str = "b-00000000-0",
    *,
    site: Site = PLAIN_SITE,
    key: str = "k",
    parallel: bool = True,
    deadline: float = 10.0,
    html: str = "<p>ready</p>",
    delta: str = "",
    asynchronous: bool = False,
    parent: str | None = None,
    depth: int | None = None,
) -> Job[str]:
    """Build a job over a plain string delta under a fixed id, rendering `html`.

    Two jobs of the same arguments are equal, and the depth follows the parent.
    """
    return Job(
        ident,
        key,
        site,
        BoundaryOptions(parallel=parallel),
        deadline,
        Failed(),
        run=None if asynchronous else Ready(html, delta),
        arun=ReadyLater(html, delta) if asynchronous else None,
        parent=parent,
        depth=int(parent is not None) if depth is None else depth,
    )
