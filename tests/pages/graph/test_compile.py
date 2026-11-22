import inspect
from pathlib import Path
from typing import Literal

import pytest
from django.contrib.auth.models import Permission

from next.deps import Depends, DObject
from next.deps.resolver import current_resolver
from next.pages import Context
from next.pages.graph import compile_graph
from next.testing import override_provider
from tests.support import bound_dependency


ROOT = Path("/graph/page.py")
MID = Path("/graph/mid/page.py")
LEAF = Path("/graph/mid/leaf/page.py")


_READS_A = Context("a")
_READS_OWN_NAME = Context()
_READS_THREE = Context(lambda: 3)
_READS_THROUGH_SOURCE = Context(lambda a: a)
_CONSTANT = Context(5)
_FACTORY = Depends(lambda: 1)
_READING_FACTORY = Depends(lambda a: a)
_CONSTANT_DEPENDS = Depends(5)

pytestmark = pytest.mark.usefixtures("framework_providers")


def _reads_title(title: str) -> str:
    return title


def _reads_post(post: str, title: str = Depends("graph_title")) -> str:
    return post + title


def _loops(value: object = None) -> object:
    return value


_loops.__defaults__ = (Depends(_loops),)


class _Everything:
    """A provider the graph cannot analyse, which may read any key."""

    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        return param.name == "custom"

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        return param.name == "custom"

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        return "custom"


class TestCompile:
    """The graph links a callable to the earlier ones whose result it reads."""

    def test_a_parameter_named_after_an_earlier_key_is_an_edge(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda a: a)

        graph = registry.graph(LEAF)

        assert graph.needs == (frozenset(), frozenset({0}))
        assert graph.layers == ((0,), (1,))

    def test_a_parameter_named_after_a_later_key_is_no_edge(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda b=None: b)
        registry.register_context(LEAF, "b", lambda: 1)

        assert registry.graph(LEAF).layers == ((0, 1),)

    def test_a_context_marker_naming_a_key_is_an_edge(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=_READS_A: value)
        registry.register_context(LEAF, "c", lambda a=_READS_OWN_NAME: a)

        graph = registry.graph(LEAF)

        assert graph.needs[1] == frozenset({0})
        assert graph.needs[2] == frozenset({0})

    def test_a_reserved_or_constant_marker_is_no_edge(self, registry) -> None:
        registry.register_context(LEAF, None, lambda: {"request": 1})
        registry.register_context(LEAF, "a", lambda request=_READS_OWN_NAME: request)
        registry.register_context(LEAF, "b", lambda value=_CONSTANT: value)

        assert registry.graph(LEAF).layers == ((0, 1, 2),)

    def test_a_marker_with_a_callable_source_reads_what_the_source_reads(
        self, registry
    ) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda: 2)
        registry.register_context(LEAF, "c", lambda value=_READS_THREE: value)
        registry.register_context(LEAF, "d", lambda value=_READS_THROUGH_SOURCE: value)

        graph = registry.graph(LEAF)

        assert graph.needs[2:] == (frozenset(), frozenset({0}))
        assert graph.reads[3] == frozenset()

    def test_parameters_no_context_read_fills_are_no_edge(self, registry) -> None:
        def reader(
            *args,
            request=None,
            a=_FACTORY,
            b=_CONSTANT_DEPENDS,
            row: DObject = None,
            **kwargs,
        ):
            return row

        registry.register_context(LEAF, None, lambda: {"a": 1})
        registry.register_context(LEAF, "b", reader)

        assert registry.graph(LEAF).layers == ((0, 1),)

    def test_a_named_dependency_reads_the_keys_its_parameters_name(
        self, registry
    ) -> None:
        registry.register_context(LEAF, "post", lambda: "p1")
        registry.register_context(LEAF, "title", lambda t=Depends("graph_post"): t)

        with (
            bound_dependency("graph_post", _reads_post),
            bound_dependency("graph_title", _reads_title),
        ):
            graph = registry.graph(LEAF)

        assert graph.needs[1] == frozenset({0})
        assert graph.reads[1] == frozenset()

    def test_a_factory_dependency_reads_the_keys_its_parameters_name(
        self, registry
    ) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=_READING_FACTORY: value)

        assert registry.graph(LEAF).needs[1] == frozenset({0})

    def test_a_dependency_cycle_reads_nothing_twice(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=Depends("graph_loop"): value)
        registry.register_context(LEAF, "c", _loops)

        with bound_dependency("graph_loop", lambda graph_loop=Depends(): graph_loop):
            graph = registry.graph(LEAF)

        assert graph.layers == ((0, 1, 2),)

    def test_an_unbound_dependency_reads_nothing(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=Depends("graph_none"): value)

        assert registry.graph(LEAF).layers == ((0, 1),)

    def test_an_object_reads_its_parent_key_and_what_its_scope_reads(
        self, registry
    ) -> None:
        def perm(permission: DObject[Permission, Literal["content_type"]]) -> object:
            return permission

        def row(row: DObject[Permission]) -> object:
            return row

        registry.register_context(LEAF, "actor", lambda: None)
        registry.register_context(LEAF, "content_type", lambda: None)
        registry.register_context(LEAF, "perm", perm)
        registry.register_context(LEAF, "row", row)

        with bound_dependency("scope", lambda actor: actor):
            graph = registry.graph(LEAF)

        assert graph.needs[2:] == (frozenset({0, 1}), frozenset({0}))

    def test_unresolved_hints_read_the_raw_annotations(self, registry) -> None:
        def reader(a=None) -> object:
            return a

        reader.__annotations__ = {"a": "Missing"}
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", reader)

        assert registry.graph(LEAF).needs[1] == frozenset({0})

    def test_a_provider_the_graph_cannot_analyse_reads_every_earlier_key(
        self, registry
    ) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda: 2)
        registry.register_context(LEAF, "c", lambda custom=None: custom)

        with override_provider(_Everything()):
            graph = registry.graph(LEAF)

        assert graph.needs[2] == frozenset({0, 1})
        assert registry.graph(LEAF).needs[2] == frozenset()

    def test_a_rebound_dependency_rebuilds_the_graph(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=Depends("graph_swap"): value)

        with bound_dependency("graph_swap", lambda: 0):
            first = registry.graph(LEAF)
            assert registry.graph(LEAF) is first
            with bound_dependency("graph_swap", lambda a: a):
                second = registry.graph(LEAF)

        assert first.needs[1] == frozenset()
        assert second.needs[1] == frozenset({0})

    def test_a_callable_after_a_keyless_one_reading_by_name_waits_for_it(
        self, registry
    ) -> None:
        registry.register_context(LEAF, None, lambda: {"spread": 1})
        registry.register_context(LEAF, "free", lambda: 2)
        registry.register_context(LEAF, "reader", lambda anything=None: anything)

        graph = registry.graph(LEAF)

        assert graph.needs == (frozenset(), frozenset(), frozenset({0}))

    def test_a_callable_without_a_signature_reads_nothing(self, registry) -> None:
        registry.register_context(LEAF, None, lambda: {"spread": 1})
        registry.register_context(LEAF, "empty", dict)

        assert registry.graph(LEAF).layers == ((0, 1),)

    def test_an_inherited_key_waits_for_the_keys_before_it(self, registry) -> None:
        registry.register_context(ROOT, "k", lambda: 1, inherit_context=True)
        registry.register_context(MID, None, lambda: {"x": 1}, inherit_context=True)
        registry.register_context(MID, "k", lambda: 2, inherit_context=True)

        graph = registry.graph(LEAF)

        assert [step.group for step in graph.steps] == [0, 1, 1]
        assert graph.needs[2] == frozenset({0, 1})

    def test_two_independent_async_callables_make_the_graph_parallel(
        self, registry
    ) -> None:
        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        registry.register_context(LEAF, "one", one)
        assert registry.graph(LEAF).parallel is False

        registry.register_context(LEAF, "two", two)
        assert registry.graph(LEAF).parallel is True

    def test_an_async_chain_stays_serial(self, registry) -> None:
        async def one() -> int:
            return 1

        async def two(one: int) -> int:
            return one + 1

        registry.register_context(LEAF, "one", one)
        registry.register_context(LEAF, "two", two)

        assert registry.graph(LEAF).parallel is False

    def test_an_empty_run_order_has_no_layer(self) -> None:
        graph = compile_graph((), frozenset(), current_resolver())

        assert graph.layers == ()
        assert graph.parallel is False

    def test_the_graph_is_built_once_per_registry_version(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        first = registry.graph(LEAF)

        assert registry.graph(LEAF) is first

        registry.register_context(LEAF, "b", lambda: 2)
        assert registry.graph(LEAF) is not first
