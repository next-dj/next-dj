import logging
from collections.abc import Generator
from pathlib import Path

import pytest
from django.contrib.auth.models import Group
from django.http import Http404
from django.test import override_settings

from next.deps import DObject
from next.deps.objects import SCOPE_DEPENDENCY
from next.pages.gating import (
    GATE_KEY,
    SLOW_SHELL,
    Plan,
    ShellGate,
    Split,
    guard_steps,
    note_slow,
    plan_gating,
)
from next.pages.graph import Blame, Phase, merged, run_phase
from next.pages.loaders import BoundaryChain, PageBoundary
from next.pages.registry import PageContextRegistry
from next.static.serializers import JsonJsContextSerializer
from next.testing import NextClient
from tests.support import bound_dependency, file_router_config_entry, routed, write_page


ROOT = Path("/gating/page.py")
MID = Path("/gating/mid/page.py")
LEAF = Path("/gating/mid/leaf/page.py")
LEAF_LOADING = Path("/gating/mid/leaf/loading.djx")
MID_LOADING = Path("/gating/mid/loading.djx")
ROOT_LOADING = Path("/gating/loading.djx")

pytestmark = pytest.mark.usefixtures("framework_providers")


def _loading(level: int, source: Path, error: Path | None = None) -> PageBoundary:
    return PageBoundary(level, level, error, source)


def _plan(
    layered_registry: PageContextRegistry,
    *boundaries: PageBoundary,
    metadata: frozenset[str] = frozenset(),
) -> Plan:
    return plan_gating(
        layered_registry.graph(LEAF),
        layered_registry.inherited_levels(LEAF),
        boundaries,
        metadata,
        LEAF,
    )


def _keys(plan: Plan, steps: frozenset[int]) -> set[str | None]:
    return {plan.graph.steps[index].key for index in steps}


def _missing() -> str:
    raise Http404


def _row(group: DObject[Group]) -> str:
    return group.name


@pytest.fixture()
def layered_registry() -> PageContextRegistry:
    """Return a registry with one inherited callable per ancestor and two own ones."""
    held = PageContextRegistry()
    held.register_context(ROOT, "site", lambda: "S", inherit_context=True)
    held.register_context(MID, "section", lambda: "M", inherit_context=True)
    held.register_context(LEAF, "body", lambda: "B")
    held.register_context(LEAF, "extra", lambda body: body + "!")
    return held


class TestSplit:
    """The innermost boundary above a callable's directory runs it, else the shell."""

    def test_a_boundary_of_the_page_directory_gates_only_the_page(
        self, layered_registry
    ) -> None:
        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert _keys(plan, plan.split.shell) == {"site", "section"}
        assert _keys(plan, plan.split.job(LEAF_LOADING)) == {"body", "extra"}

    def test_the_callables_of_the_boundary_directory_stay_in_the_shell(
        self, layered_registry
    ) -> None:
        plan = _plan(layered_registry, _loading(1, MID_LOADING))

        assert _keys(plan, plan.split.shell) == {"site", "section"}

    def test_nested_boundaries_run_each_callable_in_the_innermost_above_it(
        self, layered_registry
    ) -> None:
        plan = _plan(
            layered_registry, _loading(0, LEAF_LOADING), _loading(2, ROOT_LOADING)
        )

        assert _keys(plan, plan.split.shell) == {"site"}
        assert _keys(plan, plan.split.job(ROOT_LOADING)) == {"section"}
        assert _keys(plan, plan.split.job(LEAF_LOADING)) == {"body", "extra"}

    def test_an_unknown_loading_file_owns_nothing(self, layered_registry) -> None:
        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert plan.split.job(Path("/elsewhere/loading.djx")) == frozenset()

    def test_the_levels_name_the_directory_each_callable_feeds(
        self, layered_registry
    ) -> None:
        layered_registry.register_context(
            LEAF, "nav", lambda: "N", inherit_context=True
        )

        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert plan.levels == (2, 1, 0, -1, -1)
        assert plan.inherited == (2, 1)

    def test_an_own_inheritable_callable_feeds_the_layout_of_its_directory(
        self, layered_registry
    ) -> None:
        layered_registry.register_context(
            LEAF, "nav", lambda: "N", inherit_context=True
        )

        own = _plan(layered_registry, _loading(0, LEAF_LOADING))
        above = _plan(layered_registry, _loading(1, MID_LOADING))

        assert "nav" in _keys(own, own.split.shell)
        assert "nav" in _keys(above, above.split.job(MID_LOADING))


class TestGuards:
    """A gated guard runs in the shell, with every callable it reads."""

    def test_a_declared_guard_moves_with_what_it_reads(self, layered_registry) -> None:
        layered_registry.declare_guard(LEAF, "extra", guard=True)

        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert _keys(plan, plan.split.shell) == {"site", "section", "body", "extra"}
        assert plan.split.job(LEAF_LOADING) == frozenset()

    def test_a_scanned_guard_moves_to_the_shell(self, layered_registry) -> None:
        layered_registry.register_context(LEAF, "found", _missing)

        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert "found" in _keys(plan, plan.split.shell)
        assert _keys(plan, frozenset(plan.split.guards)) == {"found"}

    def test_a_url_addressed_row_moves_to_the_shell(self, layered_registry) -> None:
        layered_registry.register_context(LEAF, "row", _row)

        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert _keys(plan, frozenset(plan.split.guards)) == {"row"}
        assert "row" not in _keys(plan, plan.split.job(LEAF_LOADING))

    def test_a_guard_takes_every_callable_before_it_into_the_shell(
        self, layered_registry
    ) -> None:
        layered_registry.register_context(LEAF, "zz_found", _missing)

        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert _keys(plan, plan.split.shell) == {
            "site",
            "section",
            "body",
            "extra",
            "zz_found",
        }
        assert _keys(plan, frozenset(plan.split.promoted)) == {
            "body",
            "extra",
            "zz_found",
        }

    def test_a_callable_after_the_last_guard_stays_in_the_job(
        self, layered_registry
    ) -> None:
        layered_registry.register_context(LEAF, "aa_found", _missing)

        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert _keys(plan, plan.split.job(LEAF_LOADING)) == {"body", "extra"}

    def test_guard_false_keeps_a_scanned_guard_in_the_job(
        self, layered_registry
    ) -> None:
        layered_registry.register_context(LEAF, "found", _missing)
        layered_registry.declare_guard(LEAF, "found", guard=False)

        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert "found" in _keys(plan, plan.split.job(LEAF_LOADING))

    def test_debug_logs_each_promotion(self, layered_registry, caplog) -> None:
        with (
            override_settings(DEBUG=True),
            caplog.at_level(logging.DEBUG, "next.pages.gating"),
        ):
            _plan(
                layered_registry,
                _loading(0, LEAF_LOADING),
                metadata=frozenset({"body"}),
            )
            layered_registry.register_context(LEAF, "found", _missing)
            _plan(layered_registry, _loading(0, LEAF_LOADING))

        messages = [record.getMessage() for record in caplog.records]
        assert any("'_missing'" in message and "404" in message for message in messages)
        assert any("provides 'body'" in message for message in messages)
        assert any("gated @context callables" in message for message in messages)

    def test_debug_logs_nothing_for_a_plan_that_promotes_nothing(
        self, layered_registry, caplog
    ) -> None:
        with (
            override_settings(DEBUG=True),
            caplog.at_level(logging.DEBUG, "next.pages.gating"),
        ):
            plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert plan.split.promoted == ()
        assert caplog.records == []


class TestGuardSteps:
    """A reload runs every step up to the last guard, whatever the boundaries."""

    def test_a_graph_without_a_guard_runs_nothing(self, layered_registry) -> None:
        assert guard_steps(layered_registry.graph(LEAF)) == frozenset()

    def test_every_step_up_to_the_last_guard_runs(self, layered_registry) -> None:
        layered_registry.register_context(LEAF, "body_found", _missing)
        graph = layered_registry.graph(LEAF)

        steps = guard_steps(graph)

        assert {graph.steps[index].key for index in steps} == {
            "site",
            "section",
            "body",
            "body_found",
        }

    def test_an_inherited_guard_runs_alone_before_the_page(
        self, layered_registry
    ) -> None:
        layered_registry.declare_guard(ROOT, "site", guard=True)
        graph = layered_registry.graph(LEAF)

        steps = guard_steps(graph)

        assert {graph.steps[index].key for index in steps} == {"site"}


class TestMetadata:
    """A key the page metadata reads pulls its gated provider into the shell."""

    def test_a_gated_provider_is_pulled_with_what_it_reads(
        self, layered_registry
    ) -> None:
        plan = _plan(
            layered_registry, _loading(0, LEAF_LOADING), metadata=frozenset({"extra"})
        )

        assert _keys(plan, plan.split.shell) == {"site", "section", "body", "extra"}
        assert [key for _index, key in plan.split.pulled] == ["extra"]

    @pytest.mark.parametrize(
        "key",
        [
            pytest.param("site", id="the-shell-provides-it"),
            pytest.param("slug", id="no-keyed-callable-provides-it"),
        ],
    )
    def test_a_key_pulls_nothing(self, layered_registry, key: str) -> None:
        plan = _plan(
            layered_registry, _loading(0, LEAF_LOADING), metadata=frozenset({key})
        )

        assert plan.split.pulled == ()

    def test_a_key_no_keyed_callable_provides_pulls_the_gated_keyless_ones(
        self, layered_registry
    ) -> None:
        layered_registry.register_context(LEAF, None, lambda: {"title": "T"})

        plan = _plan(
            layered_registry, _loading(0, LEAF_LOADING), metadata=frozenset({"title"})
        )

        steps = plan.graph.steps
        assert [(steps[index].key, key) for index, key in plan.split.pulled] == [
            (None, "title")
        ]
        assert None in _keys(plan, plan.split.shell)
        assert _keys(plan, plan.split.job(LEAF_LOADING)) == {"body", "extra"}

    def test_a_keyless_callable_the_shell_runs_pulls_nothing_more(
        self, layered_registry
    ) -> None:
        layered_registry.register_context(
            MID, None, lambda: {"title": "T"}, inherit_context=True
        )

        plan = _plan(
            layered_registry, _loading(0, LEAF_LOADING), metadata=frozenset({"title"})
        )

        assert plan.split.pulled == ()


class TestPlan:
    """The plan answers what the checks and the shell ask of it."""

    def test_the_gated_keys_are_those_only_a_job_provides(
        self, layered_registry
    ) -> None:
        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert set(plan.gated()) == {"body", "extra"}

    def test_a_keyless_job_callable_provides_no_gated_key(
        self, layered_registry
    ) -> None:
        layered_registry.register_context(LEAF, None, lambda: {"title": "T"})

        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert set(plan.gated()) == {"body", "extra"}

    @pytest.mark.parametrize(("group", "level"), [(-1, -1), (0, 2), (1, 1), (5, -1)])
    def test_the_failed_level_reads_the_blamed_group(
        self, layered_registry, group: int, level: int
    ) -> None:
        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))
        blamed = Blame()
        blamed.group = group

        assert plan.failed_level(blamed) == level

    def test_awaits_tells_whether_a_step_is_async(self, layered_registry) -> None:
        async def later() -> str:
            return "L"

        layered_registry.register_context(LEAF, "later", later)
        plan = _plan(layered_registry, _loading(0, LEAF_LOADING))

        assert plan.awaits(plan.split.job(LEAF_LOADING)) is True
        assert plan.awaits(plan.split.shell) is False

    def test_a_split_holds_its_parts(self) -> None:
        split = Split(frozenset({0}), ((LEAF_LOADING, frozenset({1})),))

        assert split.job(LEAF_LOADING) == frozenset({1})
        assert split.guards == ()


class TestNoteSlow:
    """A slow shell names its slowest callable under `DEBUG`."""

    @pytest.fixture()
    def plan(self, layered_registry) -> Plan:
        return _plan(layered_registry, _loading(1, MID_LOADING))

    @pytest.mark.parametrize(
        ("timings", "elapsed"), [(None, 1.0), ({}, 1.0), ({0: 0.1}, SLOW_SHELL)]
    )
    def test_a_fast_or_untimed_shell_logs_nothing(
        self, plan, caplog, timings, elapsed
    ) -> None:
        with caplog.at_level(logging.DEBUG, "next.pages.gating"):
            note_slow(plan, LEAF, timings, elapsed)

        assert caplog.records == []

    def test_a_callable_of_the_boundary_directory_is_named_as_such(
        self, plan, caplog
    ) -> None:
        with caplog.at_level(logging.DEBUG, "next.pages.gating"):
            note_slow(plan, LEAF, {0: 0.01, 1: 0.3}, 0.31)

        message = caplog.records[0].getMessage()
        assert "'<lambda>'" in message
        assert "belongs to the directory of a loading.djx" in message

    def test_a_callable_above_every_boundary_is_named_as_such(
        self, plan, caplog
    ) -> None:
        with caplog.at_level(logging.DEBUG, "next.pages.gating"):
            note_slow(plan, LEAF, {0: 0.3, 1: 0.01}, 0.31)

        assert "sits above every loading.djx" in caplog.records[0].getMessage()


def _gate(
    layered_registry: PageContextRegistry, *boundaries: PageBoundary
) -> ShellGate:
    plan = _plan(layered_registry, *boundaries)
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


class TestGate:
    """Each page boundary job runs its own callables over what the shell took."""

    def test_the_level_of_a_boundary_reads_from_its_file(
        self, layered_registry
    ) -> None:
        gate = _gate(layered_registry, _loading(0, LEAF_LOADING))

        assert gate.level(LEAF_LOADING) == 0
        assert gate.level(MID_LOADING) is None

    def test_a_directory_below_the_boundary_sits_inside_its_job(
        self, layered_registry
    ) -> None:
        gate = _gate(layered_registry, _loading(1, MID_LOADING))

        assert gate.inside(MID_LOADING, 0) is True
        assert gate.inside(MID_LOADING, 1) is False
        assert gate.inside(LEAF_LOADING, 0) is False

    def test_the_blame_holds_the_inherited_results_then_the_failed_ones(
        self, layered_registry
    ) -> None:
        gate = _gate(layered_registry, _loading(0, LEAF_LOADING))
        failed = Blame()
        failed.settle(1, "late", "L")

        blame = gate.blame(failed)

        assert blame.settled == [
            (0, "site", "S"),
            (1, "section", "M"),
            (1, "late", "L"),
        ]
        assert gate.blame().cut(2) == {"site": "S", "section": "M"}

    def test_a_job_adds_the_merged_context_and_the_next_gate(
        self, layered_registry
    ) -> None:
        gate = _gate(layered_registry, _loading(0, LEAF_LOADING))

        advanced = gate.advance(LEAF_LOADING)

        assert advanced.layer["extra"] == "B!"
        assert "site" not in advanced.layer
        following = advanced.layer[GATE_KEY]
        assert isinstance(following, ShellGate)
        assert set(following.results) == {0, 1, 2, 3}
        assert following.merged.context_data["site"] == "S"

    def test_a_job_with_nothing_gated_adds_nothing(self, layered_registry) -> None:
        gate = _gate(layered_registry, _loading(0, LEAF_LOADING))

        advanced = gate.advance(MID_LOADING)

        assert advanced.layer == {}
        assert advanced.published == {}

    def test_a_job_publishes_only_what_it_serialized(self, layered_registry) -> None:
        serializer = JsonJsContextSerializer()
        layered_registry.register_context(
            LEAF, "shown", lambda: 1, serialize=True, serializer=serializer
        )
        gate = _gate(layered_registry, _loading(0, LEAF_LOADING))

        advanced = gate.advance(LEAF_LOADING)

        assert advanced.published == {"shown": 1}
        assert advanced.serializers == {"shown": serializer}

    def test_an_async_callable_runs_through_the_graph(self, layered_registry) -> None:
        async def later(body: str) -> str:
            return body + "?"

        layered_registry.register_context(LEAF, "later", later)
        gate = _gate(layered_registry, _loading(0, LEAF_LOADING))

        assert gate.advance(LEAF_LOADING).layer["later"] == "B?"


_ROW_PAGE = """
from django.contrib.auth.models import Group

from next.deps import DObject
from next.pages import context


@context("shown")
def shown(group: DObject[Group]) -> str:
    return group.name
"""
_SHELL = "<!DOCTYPE html><html><head></head><body>{% template %}</body></html>"


@pytest.fixture()
def streamed_rows(tmp_path: Path, scoped_models) -> Generator[Path, None, None]:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(_SHELL)
    shop = root / "shop"
    shop.mkdir()
    (shop / "loading.djx").write_text("<p>loading</p>")
    write_page(shop, "[int:group]", _ROW_PAGE, body="<b>{{ shown }}</b>")
    with bound_dependency(SCOPE_DEPENDENCY, lambda: "t"), routed(root):
        yield root


@pytest.mark.django_db()
@pytest.mark.usefixtures("streamed_rows")
class TestStreamedRow:
    """A row the URL addresses settles before the first byte of a streamed page."""

    @pytest.mark.parametrize(
        "name", [pytest.param("other:x", id="hidden"), pytest.param(None, id="gone")]
    )
    def test_a_row_the_scope_does_not_see_answers_404(self, name) -> None:
        pk = 999 if name is None else Group.objects.create(name=name).pk

        response = NextClient().get(f"/shop/{pk}/")

        assert response.status_code == 404

    def test_a_visible_row_renders_in_the_shell(self) -> None:
        group = Group.objects.create(name="t:x")

        page = NextClient().get_stream(f"/shop/{group.pk}/")

        assert page.response.status_code == 200
        assert "<b>t:x</b>" in page.final_html()


_TITLED_PAGE = """
from next.pages import context


@context("title")
def title() -> str:
    return "C"
"""


def title_processor(request) -> dict[str, object]:
    return {"title": "P"}


class TestProcessorKeys:
    """A context processor wins over a key a job computes, as it does inline."""

    @pytest.mark.parametrize(
        ("deferred", "streamed"),
        [(False, False), (True, False), (True, True)],
        ids=["inline", "deferred", "streamed"],
    )
    def test_the_processor_value_shows_however_the_page_renders(
        self, tmp_path: Path, *, deferred: bool, streamed: bool
    ) -> None:
        root = tmp_path / "pages"
        root.mkdir()
        (root / "layout.djx").write_text(_SHELL)
        shop = root / "shop"
        shop.mkdir()
        if deferred:
            (shop / "loading.djx").write_text("<p>loading</p>")
        write_page(shop, "item", _TITLED_PAGE, body="<b>{{ title }}</b>")
        processors = {"context_processors": [f"{__name__}.title_processor"]}
        entry = file_router_config_entry(pages_dir=root, options=processors)
        client = NextClient()
        with routed(root, PAGE_BACKENDS=[entry]):
            if streamed:
                page = client.get_stream("/shop/item/")
                assert page.chunks
                html = page.final_html()
            else:
                html = client.get("/shop/item/").content.decode()
        assert "<b>P</b>" in html
