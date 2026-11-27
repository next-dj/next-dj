from pathlib import Path
from unittest import mock

import pytest

from next.pages import Page, page
from next.pages.boundaries import SHELL_KEY, Caught
from next.pages.gating import GATE_KEY, ShellGate
from next.pages.loaders import BoundaryChain, PageBoundary, page_boundaries
from next.pages.manager import gating as manager_gating
from tests.support import build_page_request
from tests.support.pages import folded_metadata, loaded_page


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
        page_file = loaded_page(tmp_path, _GATED_SYNC)

        context = _gated(page_file, build_page_request())

        assert isinstance(context, dict)
        assert "total" not in context
        assert isinstance(context[GATE_KEY], ShellGate)
        assert SHELL_KEY in context

    def test_an_async_guard_runs_the_shell_through_the_bridge(self, tmp_path) -> None:
        (tmp_path / "loading.djx").write_text("l")
        page_file = loaded_page(tmp_path, _GATED_ASYNC)

        context = _gated(page_file, build_page_request())

        assert isinstance(context, dict)
        assert context["rows"] == [1, 2]
        assert "total" not in context
        assert str(folded_metadata(context).folded().title) == "Async"

    def test_an_async_shell_without_a_request_builds_on_the_loop(
        self, tmp_path
    ) -> None:
        (tmp_path / "loading.djx").write_text("l")
        page_file = loaded_page(tmp_path, _GATED_GUARD)

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
        page_file = loaded_page(tmp_path, _GATED_FAILING.format(kind=kind), "leaf")

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
            manager_gating, "plan_gating", side_effect=AssertionError
        ):
            assert manager.gating_plan(page_file, chain) is first
        manager.contexts.register_context(page_file, "more", lambda: 3)
        assert manager.gating_plan(page_file, chain) is not first
