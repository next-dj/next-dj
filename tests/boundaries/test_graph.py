from pathlib import Path

import pytest

from next.boundaries.graph import (
    App,
    Edge,
    Graph,
    Tangle,
    module_owner,
    path_owner,
    project_apps,
)
from tests.django_setup import PROJECT_ROOT
from tests.support import throwaway_apps


def _app(label: str, root: Path = Path("/project")) -> App:
    return App(label, label, root / label)


def _edge(source: str, target: str, line: int = 1, file: str = "a.py") -> Edge:
    return Edge("import", source, target, Path(file), line, f"{target}.models")


def _graph(*pairs: tuple[str, str]) -> Graph:
    labels = sorted({label for pair in pairs for label in pair})
    return Graph(
        tuple(_app(label) for label in labels),
        tuple(_edge(source, target) for source, target in pairs),
    )


def _routes(graph: Graph) -> list[tuple[tuple[str, ...], list[str]]]:
    return [
        (tangle.labels, [edge.source for edge in tangle.cycle])
        for tangle in graph.tangles()
    ]


class TestApp:
    """An app owns its package and every module below it, never a lookalike prefix."""

    @pytest.mark.parametrize(
        ("module", "owned"),
        [("shop", True), ("shop.models", True), ("shopping.models", False)],
    )
    def test_owns_module(self, module: str, *, owned: bool) -> None:
        assert App("shop", "shop", Path("/shop")).owns_module(module) is owned

    def test_submodule_is_the_part_below_the_package(self) -> None:
        app = App("billing", "shop.billing", Path("/shop/billing"))

        assert app.submodule("shop.billing.services.invoices") == "services.invoices"
        assert app.submodule("shop.billing") == ""


class TestGraphLookups:
    """A graph answers the app of a label and the edges leaving or reaching it."""

    def test_app_by_label(self) -> None:
        graph = _graph(("shop", "crm"))

        assert graph.app("crm") == _app("crm")
        assert graph.app("nowhere") is None

    def test_outgoing_and_incoming(self) -> None:
        graph = _graph(("shop", "crm"), ("crm", "auth"), ("blog", "crm"))

        assert [edge.target for edge in graph.outgoing("crm")] == ["auth"]
        assert [edge.source for edge in graph.incoming("crm")] == ["shop", "blog"]


class TestTangles:
    """Each strongly connected group of apps is one tangle with a shortest witness."""

    def test_an_acyclic_graph_has_none(self) -> None:
        assert _graph(("a", "b"), ("b", "c"), ("a", "c")).tangles() == ()

    def test_a_self_loop_is_no_tangle(self) -> None:
        assert _graph(("a", "a"), ("a", "b")).tangles() == ()

    def test_two_components_are_two_tangles(self) -> None:
        graph = _graph(
            ("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"), ("e", "d")
        )

        assert _routes(graph) == [
            (("a", "b", "c"), ["a", "b", "c"]),
            (("d", "e"), ["d", "e"]),
        ]

    @pytest.mark.parametrize(
        ("pairs", "route"),
        [
            ([("a", "b"), ("b", "c"), ("c", "a"), ("a", "c")], ["a", "c"]),
            ([("a", "b"), ("b", "a"), ("a", "c"), ("c", "d"), ("d", "a")], ["a", "b"]),
        ],
        ids=["shortcut_back", "short_loop_beside_a_long_one"],
    )
    def test_the_witness_is_the_shortest_cycle_through_the_first_label(
        self, pairs: list[tuple[str, str]], route: list[str]
    ) -> None:
        (tangle,) = _graph(*pairs).tangles()

        assert [edge.source for edge in tangle.cycle] == route

    def test_each_step_takes_the_first_edge_in_order(self) -> None:
        later = _edge("a", "b", line=9, file="z.py")
        first = _edge("a", "b", line=3, file="m.py")
        back = _edge("b", "a", line=1, file="n.py")
        graph = Graph((_app("a"), _app("b")), (later, back, first))

        (tangle,) = graph.tangles()

        assert tangle.cycle == (first, back)
        assert tangle.pairs == (first, back)

    def test_the_order_of_the_edges_does_not_change_the_answer(self) -> None:
        pairs = [("a", "b"), ("b", "a"), ("b", "c"), ("c", "d"), ("d", "b"), ("x", "y")]
        expected = _routes(_graph(*pairs))
        shuffled = [*pairs[3:], *pairs[:3]][::-1]

        assert _routes(_graph(*shuffled)) == expected
        assert expected == [(("a", "b", "c", "d"), ["a", "b"])]

    def test_a_long_ring_needs_no_recursion(self) -> None:
        labels = [f"app{index:05d}" for index in range(5000)]
        pairs = list(zip(labels, [*labels[1:], labels[0]], strict=True))

        (tangle,) = _graph(*pairs).tangles()

        assert tangle.labels == tuple(labels)
        assert len(tangle.cycle) == len(labels)

    def test_a_tangle_holds_its_labels_cycle_and_pairs(self) -> None:
        edge = _edge("a", "b")
        tangle = Tangle(("a", "b"), (edge,), (edge,))

        assert (tangle.labels, tangle.cycle, tangle.pairs) == (
            ("a", "b"),
            (edge,),
            (edge,),
        )

    def test_the_pairs_are_the_edges_inside_the_tangle_in_order(self) -> None:
        pairs = [("b", "c"), ("c", "a"), ("a", "b"), ("a", "c"), ("c", "x"), ("y", "a")]
        edges = tuple(
            _edge(source, target, line=line)
            for line, (source, target) in enumerate(pairs, start=1)
        )
        graph = Graph(tuple(_app(label) for label in "abcxy"), edges)

        (tangle,) = graph.tangles()

        assert [(edge.source, edge.target) for edge in tangle.pairs] == [
            ("b", "c"),
            ("c", "a"),
            ("a", "b"),
            ("a", "c"),
        ]


class TestOwners:
    """The innermost app holding a path or a module owns it."""

    def test_path_owner_prefers_the_nested_app(self) -> None:
        outer = App("shop", "shop", Path("/p/shop"))
        inner = App("billing", "shop.billing", Path("/p/shop/billing"))
        apps = (outer, inner)

        assert path_owner(apps, Path("/p/shop/billing/models.py")) == inner
        assert path_owner(apps, Path("/p/shop/models.py")) == outer
        assert path_owner(apps, Path("/elsewhere/models.py")) is None

    def test_module_owner_prefers_the_nested_app(self) -> None:
        outer = App("shop", "shop", Path("/p/shop"))
        inner = App("billing", "shop.billing", Path("/p/shop/billing"))
        apps = (inner, outer)

        assert module_owner(apps, "shop.billing.models") == inner
        assert module_owner(apps, "shop.views") == outer
        assert module_owner(apps, "django.db") is None


class TestProjectApps:
    """Only apps under `BASE_DIR` are nodes, never a distribution, next.dj or Django."""

    def test_no_base_dir_is_no_app(self) -> None:
        assert project_apps(None) == ()

    def test_only_the_apps_under_the_base_dir(self, tmp_path) -> None:
        base = tmp_path / "project"
        vendored = base / "lib" / "site-packages"
        outside = tmp_path / "outside"
        for directory in (base, vendored, outside):
            directory.mkdir(parents=True, exist_ok=True)
        with throwaway_apps(base) as make_inside:
            second, first = make_inside(), make_inside()
            with throwaway_apps(vendored) as make_vendored:
                make_vendored()
                with throwaway_apps(outside) as make_outside:
                    make_outside()
                    found = project_apps(base)

        assert [app.label for app in found] == sorted([first, second])
        assert found[0].path == (base / found[0].name).resolve()

    def test_the_framework_and_installed_django_are_never_nodes(self) -> None:
        assert project_apps(PROJECT_ROOT) == ()
