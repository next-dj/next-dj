import asyncio
import threading
import time
from collections.abc import Callable, Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group, Permission

from next.components import components_manager
from next.components.modules import ModuleLoader
from next.deps import (
    REQUEST_DEP_CACHE_ATTR,
    Depends,
    DObject,
    RegisteredParameterProvider,
    provider_registry,
)
from next.deps.cache import RequestMemo, request_memo
from next.deps.objects import SCOPE_DEPENDENCY
from next.deps.resolver import DependencyResolver
from next.deps.runner import run_on_request_thread, segment_runner
from next.deps.scope import RequestScope, render_scope
from next.pages import Context, page
from next.pages.registry import PageContextRegistry
from next.testing import make_resolution_context
from tests.support import (
    bound_dependency,
    build_page_request,
    restored_provider_registry,
    unified_view,
    write_page_chain,
)
from tests.support.components import build_composite_component
from tests.support.graphs import LEAF, MID


_DELAY = 0.02


@pytest.fixture(autouse=True)
def _framework_providers() -> Generator[None, None, None]:
    """Keep only shipped providers, so a context graph reads no unknown one."""
    with restored_provider_registry():
        provider_registry.replace(
            [cls for cls in provider_registry if cls.__module__.startswith("next.")]
        )
        yield


class _Counted:
    """A dependency of either colour that counts its calls and returns a new object."""

    def __init__(self, *, fails: bool = False) -> None:
        """Start with no call counted, raising on each call when `fails`."""
        self.calls = 0
        self.fails = fails
        self._lock = threading.Lock()

    def _made(self) -> object:
        with self._lock:
            self.calls += 1
        if self.fails:
            msg = "dependency failed"
            raise RuntimeError(msg)
        return object()

    def sync(self) -> object:
        time.sleep(_DELAY)
        return self._made()

    async def async_(self) -> object:
        await asyncio.sleep(_DELAY)
        return self._made()

    def colour(self, colour: str) -> Callable[[], object]:
        return self.sync if colour == "sync" else self.async_


def _pool_runner(pool: ThreadPoolExecutor):
    """Return a segment runner that runs each segment on a thread of `pool`."""

    def run(segment):
        return asyncio.get_running_loop().run_in_executor(pool, segment)

    return run


@contextmanager
def _runner(kind: str) -> Iterator[None]:
    """Install the request-thread runner or a pool runner for the block."""
    if kind == "request":
        token = segment_runner.set(run_on_request_thread)
        try:
            yield
        finally:
            segment_runner.reset(token)
        return
    with ThreadPoolExecutor(4) as pool:
        token = segment_runner.set(_pool_runner(pool))
        try:
            yield
        finally:
            segment_runner.reset(token)


def _consumer(colour: str, name: str) -> Callable[..., object]:
    """Return a context callable of `colour` that asks for `Depends(name)`."""
    if colour == "sync":

        def sync_consumer(value=Depends(name)) -> object:
            return value

        return sync_consumer

    async def async_consumer(value=Depends(name)) -> object:
        await asyncio.sleep(0)
        return value

    return async_consumer


async def _pad() -> int:
    await asyncio.sleep(0)
    return 0


def _collect(
    registry: PageContextRegistry, url_kwargs: dict[str, object] | None = None
) -> dict[str, object]:
    async def main() -> dict[str, object]:
        async with render_scope({}):
            result = await registry.acollect_context(
                LEAF, None, url_kwargs or {}, dep_cache={}
            )
            return result.context_data

    return asyncio.run(main())


CONSUMERS = [
    pytest.param(("async", "async"), id="async-contexts"),
    pytest.param(("sync", "sync"), id="sync-contexts"),
    pytest.param(("sync", "async"), id="mixed-contexts"),
]
COLOURS = [pytest.param("sync", id="sync-dep"), pytest.param("async", id="async-dep")]
RUNNERS = [
    pytest.param("request", id="request-thread"),
    pytest.param("pool", id="pool"),
]


class TestOncePerRender:
    """A dependency two context callables of one render name runs once for both."""

    @pytest.mark.parametrize("runner", RUNNERS)
    @pytest.mark.parametrize("colour", COLOURS)
    @pytest.mark.parametrize("consumers", CONSUMERS)
    def test_a_shared_dependency_runs_once_whatever_its_colour(
        self, consumers, colour, runner
    ) -> None:
        counted = _Counted()
        registry = PageContextRegistry()
        registry.register_context(LEAF, "one", _consumer(consumers[0], "memo_tenant"))
        registry.register_context(LEAF, "two", _consumer(consumers[1], "memo_tenant"))
        registry.register_context(LEAF, "pad_a", _pad)
        registry.register_context(LEAF, "pad_b", _pad)
        with bound_dependency("memo_tenant", counted.colour(colour)), _runner(runner):
            graph = registry.graph(LEAF)
            data = _collect(registry)
        assert graph.parallel
        assert graph.shared == frozenset({"memo_tenant"})
        assert counted.calls == 1
        assert data["one"] is data["two"]

    @pytest.mark.parametrize("runner", RUNNERS)
    @pytest.mark.parametrize("colour", COLOURS)
    def test_a_shared_key_under_an_unshared_one_runs_once(self, colour, runner) -> None:
        counted = _Counted()

        def memo_user(tenant=Depends("memo_tenant")) -> object:
            return tenant

        async def reader(user=Depends("memo_user")) -> object:
            return user

        registry = PageContextRegistry()
        registry.register_context(LEAF, "one", reader)
        registry.register_context(LEAF, "two", _consumer("async", "memo_tenant"))
        with (
            bound_dependency("memo_tenant", counted.colour(colour)),
            bound_dependency("memo_user", memo_user),
            _runner(runner),
        ):
            data = _collect(registry)
        assert counted.calls == 1
        assert data["one"] is data["two"]

    @pytest.mark.parametrize("colour", COLOURS)
    def test_a_failure_reaches_both_consumers_from_one_call(self, colour) -> None:
        counted = _Counted(fails=True)
        registry = PageContextRegistry()
        registry.register_context(LEAF, "one", _consumer("async", "memo_tenant"))
        registry.register_context(LEAF, "two", _consumer("async", "memo_tenant"))
        with (
            bound_dependency("memo_tenant", counted.colour(colour)),
            _runner("pool"),
            pytest.raises(RuntimeError, match="dependency failed"),
        ):
            _collect(registry)
        assert counted.calls == 1

    @pytest.mark.parametrize(
        ("order", "ran"),
        [
            pytest.param(("plain", "reader"), ["plain"], id="reader-last"),
            pytest.param(("reader", "plain"), [], id="reader-first"),
        ],
    )
    def test_a_sync_callable_past_a_failing_shared_key_does_not_run(
        self, order, ran
    ) -> None:
        counted = _Counted(fails=True)
        called: list[str] = []

        def plain() -> int:
            called.append("plain")
            return 1

        callables = {"plain": plain, "reader": _consumer("sync", "memo_tenant")}
        registry = PageContextRegistry()
        for position, name in enumerate(order):
            registry.register_context(LEAF, f"{position}_{name}", callables[name])
        registry.register_context(LEAF, "two", _consumer("async", "memo_tenant"))
        registry.register_context(LEAF, "pad", _pad)
        with (
            bound_dependency("memo_tenant", counted.colour("sync")),
            _runner("pool"),
            pytest.raises(RuntimeError, match="dependency failed"),
        ):
            _collect(registry)
        assert counted.calls == 1
        assert called == ran


def _label(tenant=Depends("memo_tenant")) -> object:
    return tenant


_LABEL = Context(_label)
_PARENT = "permissions"


class TestOncePerRenderThroughProviders:
    """A shared key a provider reaches beyond `Depends` runs once for the render."""

    @pytest.mark.parametrize("runner", RUNNERS)
    def test_a_key_a_callable_context_source_reaches_runs_once(self, runner) -> None:
        counted = _Counted()

        async def one(value=_LABEL) -> object:
            return value

        async def two(value=_LABEL) -> object:
            return value

        registry = PageContextRegistry()
        registry.register_context(LEAF, "one", one)
        registry.register_context(LEAF, "two", two)
        with bound_dependency("memo_tenant", counted.sync), _runner(runner):
            graph = registry.graph(LEAF)
            data = _collect(registry)
        assert graph.shared == frozenset({"memo_tenant"})
        assert counted.calls == 1
        assert data["one"] is data["two"]

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.parametrize("runner", RUNNERS)
    def test_the_scope_of_parented_rows_runs_once(self, scoped_models, runner) -> None:
        editors = Group.objects.create(name="auth:editors")
        owner = Permission.objects.get(codename="change_group")
        editors.permissions.add(owner)
        scopes: list[object] = []

        def scope() -> str:
            scopes.append(object())
            return "auth"

        async def one(group: DObject[Group, _PARENT]) -> Group:
            return group

        async def two(group: DObject[Group, _PARENT]) -> Group:
            return group

        registry = PageContextRegistry()
        registry.register_context(
            MID, "permissions", lambda: owner, inherit_context=True
        )
        registry.register_context(LEAF, "one", one)
        registry.register_context(LEAF, "two", two)
        with bound_dependency(SCOPE_DEPENDENCY, scope), _runner(runner):
            graph = registry.graph(LEAF)
            data = _collect(registry, {"group": editors.pk})
        assert graph.parallel
        assert SCOPE_DEPENDENCY in graph.shared
        assert data["one"] == data["two"] == editors
        assert len(scopes) == 1


def _plan_consumers(name: str):
    async def left(value=Depends(name)) -> object:
        return value

    async def right(value=Depends(name)) -> object:
        return value

    async def both(a=Depends(left), b=Depends(right)) -> tuple[object, object]:
        return a, b

    return both


class TestOncePerCall:
    """Two concurrent branches of one call that name a key share one run of it."""

    @pytest.mark.parametrize("runner", RUNNERS)
    @pytest.mark.parametrize("colour", COLOURS)
    def test_a_key_two_branches_name_runs_once(self, colour, runner) -> None:
        counted = _Counted()
        instance = DependencyResolver()
        instance.register_dependency("memo_tenant", counted.colour(colour))
        both = _plan_consumers("memo_tenant")

        async def main() -> tuple[object, object]:
            async with render_scope({}):
                return await instance.acall(both, make_resolution_context())

        with _runner(runner):
            first, second = asyncio.run(main())
        assert counted.calls == 1
        assert first is second

    @pytest.mark.parametrize("colour", COLOURS)
    def test_a_failure_is_kept_by_the_scope_and_never_cached(self, colour) -> None:
        counted = _Counted(fails=True)
        instance = DependencyResolver()
        instance.register_dependency("memo_tenant", counted.colour(colour))
        consumer = _consumer("async", "memo_tenant")
        memo = RequestMemo(RequestScope(), shared=frozenset({"memo_tenant"}))
        contexts = [make_resolution_context() for _ in range(3)]

        async def main() -> list[object]:
            token = request_memo.set(memo)
            try:
                concurrent = await asyncio.gather(
                    *(instance.acall(consumer, context) for context in contexts[:2]),
                    return_exceptions=True,
                )
                later = await asyncio.gather(
                    instance.acall(consumer, contexts[2]), return_exceptions=True
                )
            finally:
                request_memo.reset(token)
            await memo.scope.close()
            return [*concurrent, *later]

        with _runner("pool"):
            errors = asyncio.run(main())
        assert all(isinstance(error, RuntimeError) for error in errors)
        assert errors[0] is errors[1] is errors[2]
        assert counted.calls == 1
        assert all("memo_tenant" not in context.cache for context in contexts)


COUNTING_PAGE = """
from next.deps import Depends
from next.pages import context


@context("title")
def title(value=Depends("memo_label")):
    return value


@context("items")
def items():
    return ["alpha", "beta"]
"""
COUNTING_COMPONENT = """
from next.components import context
from next.deps import Depends


@context("upper")
def upper(value=Depends("memo_label")):
    return value
"""


class TestComponentUnit:
    """A component instance is a render unit of its own on a plain GET."""

    @pytest.mark.parametrize(
        ("dispatch", "awaited", "calls"),
        [
            pytest.param(False, False, 3, id="get"),
            pytest.param(False, True, 3, id="get-async-page"),
            pytest.param(True, False, 1, id="dispatch"),
        ],
    )
    def test_an_instance_reads_no_value_the_page_resolved_outside_a_dispatch(
        self, tmp_path: Path, *, dispatch: bool, awaited: bool, calls: int
    ) -> None:
        made: list[int] = []

        def memo_label() -> str:
            made.append(1)
            return "LABEL"

        _mgr, info, module_path = build_composite_component(
            tmp_path / "badge", name="badge", template="<b>{{ upper }}</b>"
        )
        module_path.write_text(COUNTING_COMPONENT)
        ModuleLoader().load(module_path)
        source = (
            COUNTING_PAGE.replace("def title", "async def title")
            if awaited
            else COUNTING_PAGE
        )
        (leaf,) = write_page_chain(tmp_path, [("items", source)])
        (leaf.parent / "template.djx").write_text(
            '{% for x in items %}{% component "badge" label=x %}{% endfor %}'
        )
        view = unified_view(page, leaf)
        with (
            bound_dependency("memo_label", memo_label),
            patch.object(components_manager, "get_component", return_value=info),
        ):
            request = build_page_request()
            if dispatch:
                setattr(request, REQUEST_DEP_CACHE_ATTR, {})
            response = view(request)
        assert response.content.decode().count("<b>LABEL</b>") == 2
        assert len(made) == calls


class TestSeed:
    """`aseed` resolves on the loop only the keys the request memo shares."""

    @pytest.mark.parametrize(
        ("memo", "calls"),
        [
            pytest.param(None, 0, id="no-memo"),
            pytest.param(frozenset(), 0, id="nothing-shared"),
            pytest.param(frozenset({"memo_tenant"}), 1, id="shared"),
        ],
    )
    def test_only_a_shared_key_is_resolved(self, memo, calls) -> None:
        counted = _Counted()
        instance = DependencyResolver()
        instance.register_dependency("memo_tenant", counted.sync)
        consumer = _consumer("sync", "memo_tenant")
        context = make_resolution_context()

        async def main() -> None:
            held = None if memo is None else RequestMemo(RequestScope(), shared=memo)
            token = request_memo.set(held)
            try:
                await instance.aseed(consumer, context)
                await instance.aseed(_pad, context)
            finally:
                request_memo.reset(token)
            if held is not None:
                await held.scope.close()

        with _runner("pool"):
            asyncio.run(main())
        assert counted.calls == calls
        assert ("memo_tenant" in context.cache) is bool(calls)


class _Maybe:
    """A provider deferring on `v` that never claims it, so `v` stays uncertain."""

    def can_handle(self, param, _context) -> bool:
        return False

    def static_can_handle(self, param) -> bool | None:
        return None if param.name == "v" else False

    def resolve(self, param, _context) -> object:
        return None


def _looping(v: object = None) -> object:
    return v


_LOOPING = Context(_looping)
_looping.__defaults__ = (_LOOPING,)


class TestReachContract:
    """The plan compiler reads what a provider reaches through `static_reach`."""

    def test_a_provider_reporting_nothing_reaches_no_key(self) -> None:
        class Silent(RegisteredParameterProvider):
            def can_handle(self, param, _context) -> bool:
                return param.name == "silent"

            def static_can_handle(self, param) -> bool:
                return param.name == "silent"

            def resolve(self, param, _context) -> object:
                return "quiet"

        async def consumer(silent: object) -> object:
            return silent

        instance = DependencyResolver(Silent())
        plan = instance._aplan_for(consumer)
        assert plan.sources == ()
        context = make_resolution_context()
        assert asyncio.run(instance.acall(consumer, context)) == "quiet"

    def test_an_inline_cycle_a_deferring_provider_may_break_compiles(self) -> None:
        async def consumer(v=_LOOPING) -> object:
            return v

        instance = DependencyResolver(_Maybe(), *DependencyResolver().providers)
        plan = instance._aplan_for(consumer)
        assert plan.sources == ()
