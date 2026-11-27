from pathlib import Path
from urllib.parse import quote

from next.pages.boundaries import ErrorContext
from next.pages.gating import ShellGate, plan_gating
from next.pages.graph import Blame, Phase, merged, run_phase
from next.pages.loaders import BoundaryChain, PageBoundary
from next.pages.registry import PageContextRegistry


LEAF = Path("/deferred/leaf/page.py")
SITE = Path("/deferred/page.py")


def boom() -> str:
    msg = "body"
    raise ValueError(msg)


def boundary_tag(source: Path, body: str, wrapper: str = "div") -> str:
    """Return a page boundary tag of the `error.djx` at `source` around `body`."""
    shown = quote(str(source), safe="/")
    return f'{{% next:boundary "{shown}" {wrapper} %}}{body}{{% /next:boundary %}}'


def deferred_tag(
    loading: Path, body: str, error: Path | None = None, wrapper: str = "div"
) -> str:
    """Return a deferred page boundary tag of `loading` around `body`."""
    shown = "" if error is None else quote(str(error), safe="/")
    source = quote(str(loading), safe="/")
    opening = f'{{% next:boundary "{shown}" {wrapper} "{source}" %}}'
    return f"{opening}{body}{{% /next:boundary %}}"


def error_shell(source: Path, **head: object) -> ErrorContext:
    """Return the shell of one boundary at `source` over `head`."""
    chain = BoundaryChain.of((PageBoundary(0, 0, source, None),))
    return ErrorContext(head, {}, (), Blame(), chain)


def shell_gate(loading: Path, error: Path | None, *, level: int = 0) -> ShellGate:
    """Return the gate of a leaf page whose job at `level` owns its callables."""
    registry = PageContextRegistry()
    registry.register_context(SITE, "site", lambda: "S", inherit_context=True)
    registry.register_context(LEAF, "word", lambda: "W")
    registry.register_context(LEAF, "shown", lambda: 1, serialize=True)
    deferred = PageBoundary(level, level, None, loading)
    boundaries = (deferred,)
    if error is not None:
        boundaries = (deferred, PageBoundary(level + 1, level + 1, error, None))
    plan = plan_gating(
        registry.graph(LEAF),
        registry.inherited_levels(LEAF),
        (deferred,),
        frozenset(),
        LEAF,
    )
    results = run_phase(
        plan.graph,
        LEAF,
        Phase(plan.split.shell, {}),
        request=None,
        url_kwargs={},
        dep_cache={},
    )
    chain = BoundaryChain.of(boundaries)
    taken = merged(plan.graph, LEAF, results)
    return ShellGate(plan, chain, LEAF, None, {}, {}, results, taken, frozenset())
