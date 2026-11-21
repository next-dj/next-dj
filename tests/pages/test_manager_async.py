import asyncio
from pathlib import Path
from unittest import mock

import pytest
from django.http import HttpResponse

import next.pages.manager as manager_module
from next.pages import Page, page
from next.pages.boundaries import SHELL_KEY, Caught
from next.pages.gating import GATE_KEY, Gate
from next.pages.loaders import (
    BoundaryChain,
    PageBoundary,
    load_page_module,
    page_boundaries,
)
from next.pages.manager import Built
from next.pages.metadata.chain import FoldedMetadata, MetadataThunk
from next.seeding import METADATA_KEY, REQUEST_KEY
from tests.support import build_page_request, write_page


_ASYNC_CONTEXT = (
    "from next.pages import context\n\n"
    "@context('rows')\n"
    "async def rows():\n"
    "    return [1, 2]\n\n"
    "@context('total')\n"
    "def total(rows):\n"
    "    return len(rows)\n\n"
    "template = '{{ total }}'\n"
)

_ASYNC_METADATA = (
    "from next.pages import page\n\n"
    "@page.metadata\n"
    "async def meta(request):\n"
    "    return {'title': 'Async'}\n\n"
    "template = 'ok'\n"
)

_FAILING_METADATA = (
    "from next.pages import page\n\n"
    "@page.metadata\n"
    "async def meta():\n"
    "    raise LookupError\n\n"
    "template = 'ok'\n"
)


async def _late() -> int:
    return 1


def _loaded(root: Path, source: str, trail: str = "") -> Path:
    """Write and import one `page.py`, so its registrations reach the global page."""
    page_file = write_page(root, trail, source)
    _module, error = load_page_module(page_file)
    assert error is None
    return page_file


def _metadata(context: dict[str, object]) -> FoldedMetadata:
    thunk = context[METADATA_KEY]
    assert isinstance(thunk, FoldedMetadata)
    return thunk


class TestPageColour:
    """A page awaits when its run order or its metadata chain holds async code."""

    def test_a_sync_page_does_not_await_and_the_answer_is_memoised(
        self, tmp_path
    ) -> None:
        page_file = _loaded(tmp_path, "template = 'ok'\n")

        assert page.awaits(page_file) is False
        with mock.patch.object(page.contexts, "awaits", side_effect=AssertionError):
            assert page.awaits(page_file) is False

    def test_an_async_context_makes_the_page_await(self, tmp_path) -> None:
        assert page.awaits(_loaded(tmp_path, _ASYNC_CONTEXT)) is True

    def test_an_async_metadata_callable_makes_the_page_await(self, tmp_path) -> None:
        assert page.awaits(_loaded(tmp_path, _ASYNC_METADATA)) is True

    def test_a_write_to_either_registry_reads_the_colour_again(self, tmp_path) -> None:
        instance = Page()
        page_file = tmp_path / "page.py"
        assert instance.awaits(page_file) is False

        instance.reset_metadata()
        with mock.patch.object(instance.contexts, "awaits", return_value=True):
            assert instance.awaits(page_file) is True

        instance.contexts.register_context(page_file, "late", _late)
        with mock.patch.object(instance.contexts, "awaits", return_value=False):
            assert instance.awaits(page_file) is False


class TestBuildRenderContext:
    """A render context of an async page is built through the bound driver."""

    def test_the_async_context_merges_with_the_processors(self, tmp_path) -> None:
        page_file = _loaded(tmp_path, _ASYNC_CONTEXT)
        request = build_page_request()

        context = page.build_render_context(page_file, request)

        assert context["rows"] == [1, 2]
        assert context["total"] == 2
        assert context[REQUEST_KEY] is request
        assert context["request"] is request
        assert isinstance(context[METADATA_KEY], MetadataThunk)

    def test_a_render_without_a_request_runs_no_blocking_tail(self, tmp_path) -> None:
        page_file = _loaded(
            tmp_path,
            "from next.pages import context\n\n"
            "@context('rows')\n"
            "async def rows():\n    return [1]\n\n"
            "template = 'ok'\n",
        )

        with mock.patch.object(
            manager_module, "run_segment", side_effect=AssertionError
        ):
            context = page.build_render_context(page_file)

        assert context["rows"] == [1]
        assert REQUEST_KEY not in context

    def test_async_metadata_folds_ahead_of_the_render(self, tmp_path) -> None:
        page_file = _loaded(tmp_path, _ASYNC_METADATA)

        context = page.build_render_context(page_file, build_page_request())

        assert str(_metadata(context).folded().title) == "Async"

    def test_async_metadata_folds_without_a_request(self, tmp_path) -> None:
        page_file = _loaded(tmp_path, _ASYNC_METADATA)

        context = page.build_render_context(page_file)

        assert str(_metadata(context).folded().title) == "Async"

    def test_a_failing_async_metadata_callable_is_left_out(self, tmp_path) -> None:
        page_file = _loaded(tmp_path, _FAILING_METADATA)

        context = page.build_render_context(page_file, build_page_request())

        assert _metadata(context).folded().title is None

    def test_a_dict_below_an_async_ancestor_folds_in_order(self, tmp_path) -> None:
        _loaded(
            tmp_path,
            "from next.pages import page\n\n"
            "@page.metadata(inherit=True)\n"
            "async def meta():\n"
            "    return {'title': 'Root', 'description': 'Root text'}\n\n"
            "template = 'ok'\n",
        )
        leaf = _loaded(
            tmp_path, "metadata = {'description': 'Leaf text'}\ntemplate = 'ok'\n", "a"
        )

        context = page.build_render_context(leaf, build_page_request())

        assert _metadata(context).folded().description == "Leaf text"

    def test_a_refused_chain_keeps_the_lazy_thunk(self, tmp_path) -> None:
        page_file = _loaded(
            tmp_path,
            "from next.pages import page\n\n"
            "metadata = {'title': 'Dict'}\n\n"
            "@page.metadata\n"
            "async def meta():\n"
            "    return {'title': 'Callable'}\n\n"
            "template = 'ok'\n",
        )

        context = page.build_render_context(page_file, build_page_request())

        assert type(context[METADATA_KEY]) is MetadataThunk


class TestAsyncBody:
    """An `async def render` is awaited, any other body resolves in one segment."""

    def test_an_async_render_body_is_dynamic(self, tmp_path) -> None:
        page_file = _loaded(
            tmp_path, "async def render(request):\n    return '<b>async</b>'\n"
        )
        module, _error = load_page_module(page_file)

        resolution = asyncio.run(
            page.aresolve_page_body(
                page_file, module, build_page_request(), dep_cache={}
            )
        )

        assert resolution.body == "<b>async</b>"
        assert resolution.dynamic is True

    def test_an_async_render_returning_no_body_is_refused(self, tmp_path) -> None:
        page_file = _loaded(tmp_path, "async def render():\n    return 1\n")
        module, _error = load_page_module(page_file)

        with pytest.raises(TypeError, match="must return str or HttpResponseBase"):
            asyncio.run(page.aresolve_page_body(page_file, module, dep_cache={}))

    def test_a_static_body_resolves_in_one_segment(self, tmp_path) -> None:
        page_file = _loaded(tmp_path, "template = '<p>static</p>'\n")
        module, _error = load_page_module(page_file)

        resolution = asyncio.run(
            page.aresolve_page_body(page_file, module, dep_cache={})
        )

        assert resolution.body == "<p>static</p>"
        assert resolution.dynamic is False

    def test_an_authorization_awaits_an_async_render(self, tmp_path) -> None:
        page_file = _loaded(
            tmp_path,
            "from django.http import HttpResponse\n\n"
            "async def render(request):\n"
            "    return HttpResponse(status=403)\n",
        )

        denial, dynamic = page.authorization_outcome(
            page_file, build_page_request(), "/"
        )

        assert isinstance(denial, HttpResponse)
        assert denial.status_code == 403
        assert dynamic is False

    def test_a_prepared_context_skips_the_collection(self, tmp_path) -> None:
        page_file = _loaded(tmp_path, "template = '{{ value }}'\n")

        with mock.patch.object(page.contexts, "collect_context") as collect:
            html = page.render_composed(
                page_file,
                "{{ value }}",
                dep_cache={},
                built=Built({"value": "ready"}, 0.0),
            )

        assert html == "ready"
        assert collect.call_count == 0

    def test_a_render_builds_an_async_context_through_the_bridge(
        self, tmp_path
    ) -> None:
        page_file = _loaded(tmp_path, _ASYNC_CONTEXT)

        with mock.patch.object(
            manager_module, "run_bridged", wraps=manager_module.run_bridged
        ) as bridged:
            html = page.render(page_file, build_page_request())

        assert html == "2"
        assert bridged.call_count == 1


_FAILING_ASYNC = (
    "from next.pages import context\n\n"
    "@context('rows')\n"
    "async def rows():\n"
    "    raise LookupError\n\n"
    "template = '{{ rows }}'\n"
)


class TestGuardedContext:
    """An async page under a page boundary builds its context inside the guard."""

    def test_async_metadata_folds_once_the_guard_settled(self, tmp_path) -> None:
        (tmp_path / "error.djx").write_text("e")
        page_file = _loaded(tmp_path, _ASYNC_METADATA)

        context = page.guarded_context(page_file, build_page_request())

        assert isinstance(context, dict)
        assert str(_metadata(context).folded().title) == "Async"
        assert SHELL_KEY in context

    def test_a_render_without_a_request_builds_on_the_loop(self, tmp_path) -> None:
        (tmp_path / "error.djx").write_text("e")
        page_file = _loaded(tmp_path, _ASYNC_CONTEXT)

        context = page.guarded_context(page_file)

        assert isinstance(context, dict)
        assert context["total"] == 2

    def test_a_contained_failure_comes_back_with_its_shell(self, tmp_path) -> None:
        (tmp_path / "error.djx").write_text("e")
        page_file = _loaded(tmp_path, _FAILING_ASYNC)

        caught = page.guarded_context(page_file, build_page_request())

        assert isinstance(caught, Caught)
        assert isinstance(caught.error, LookupError)
        assert caught.shell is not None
        assert REQUEST_KEY in caught.shell.tail


_GATED_ASYNC = (
    "from next.pages import context, page\n\n"
    "@context('rows', guard=True)\n"
    "async def rows():\n"
    "    return [1, 2]\n\n"
    "@context('total')\n"
    "def total(rows):\n"
    "    return len(rows)\n\n"
    "@page.metadata\n"
    "async def meta(request):\n"
    "    return {'title': 'Async'}\n\n"
    "template = '{{ total }}'\n"
)

_GATED_GUARD = (
    "from next.pages import context\n\n"
    "@context('rows', guard=True)\n"
    "async def rows():\n"
    "    return [1, 2]\n\n"
    "template = 'ok'\n"
)

_GATED_SYNC = (
    "from next.pages import context\n\n"
    "@context('total')\n"
    "def total():\n"
    "    return 2\n\n"
    "template = '{{ total }}'\n"
)

_GATED_FAILING = (
    "from next.pages import context\n\n"
    "@context('rows', guard=True)\n"
    "{kind}def rows():\n"
    "    raise LookupError\n\n"
    "template = 'ok'\n"
)


def _gated(page_file: Path, request: object = None) -> dict[str, object] | Caught:
    return page.gated_context(
        page_file, request, {}, dep_cache={}, chain=page_boundaries(page_file)
    )


class TestGatedContext:
    """A page with a `loading.djx` builds a shell of the callables it does not gate."""

    def test_a_sync_shell_leaves_the_gated_callables_to_the_gate(
        self, tmp_path
    ) -> None:
        (tmp_path / "loading.djx").write_text("l")
        page_file = _loaded(tmp_path, _GATED_SYNC)

        context = _gated(page_file, build_page_request())

        assert isinstance(context, dict)
        assert "total" not in context
        assert isinstance(context[GATE_KEY], Gate)
        assert SHELL_KEY in context

    def test_an_async_guard_runs_the_shell_through_the_bridge(self, tmp_path) -> None:
        (tmp_path / "loading.djx").write_text("l")
        page_file = _loaded(tmp_path, _GATED_ASYNC)

        context = _gated(page_file, build_page_request())

        assert isinstance(context, dict)
        assert context["rows"] == [1, 2]
        assert "total" not in context
        assert str(_metadata(context).folded().title) == "Async"

    def test_an_async_shell_without_a_request_builds_on_the_loop(
        self, tmp_path
    ) -> None:
        (tmp_path / "loading.djx").write_text("l")
        page_file = _loaded(tmp_path, _GATED_GUARD)

        context = _gated(page_file)

        assert isinstance(context, dict)
        assert context["rows"] == [1, 2]

    @pytest.mark.parametrize("kind", ["", "async "])
    def test_a_failing_guard_comes_back_with_its_shell(
        self, tmp_path, kind: str
    ) -> None:
        (tmp_path / "error.djx").write_text("e")
        (tmp_path / "leaf").mkdir()
        (tmp_path / "leaf" / "loading.djx").write_text("l")
        page_file = _loaded(tmp_path, _GATED_FAILING.format(kind=kind), "leaf")

        caught = _gated(page_file, build_page_request())

        assert isinstance(caught, Caught)
        assert isinstance(caught.error, LookupError)
        assert caught.shell is not None

    def test_the_split_is_computed_once_per_page(self, tmp_path) -> None:
        manager = Page()
        page_file = tmp_path / "page.py"
        manager.contexts.register_context(page_file, "total", lambda: 2)
        chain = BoundaryChain.of((PageBoundary(0, 0, None, tmp_path / "loading.djx"),))

        first = manager.gating_plan(page_file, chain)

        with mock.patch.object(
            manager_module, "plan_gating", side_effect=AssertionError
        ):
            assert manager.gating_plan(page_file, chain) is first
        manager.contexts.register_context(page_file, "more", lambda: 3)
        assert manager.gating_plan(page_file, chain) is not first
