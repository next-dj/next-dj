import gc
import weakref
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from django.http import Http404, HttpRequest
from django.test import override_settings

from next.pages import Page, page
from next.pages.boundaries import SHELL_KEY, Caught
from next.pages.manager import render as manager_render
from next.pages.metadata import ResolvedMetadata
from next.pages.metadata.chain import MetadataThunk
from next.requests import next_of
from next.seeding import METADATA_KEY, REQUEST_KEY
from next.testing import override_next_settings
from tests.support import (
    bound_dependency,
    build_nested_page,
    build_page_request,
    build_zone_request,
    path_under,
    record_path_calls,
    resolve_page_metadata,
    touch_later,
    unified_view,
    write_page_chain,
)
from tests.support.pages import folded_metadata, loaded_page, page_calls
from tests.support.views import write_dynamic_page


def _silent_processor(request) -> None:
    return None


def _failing_processor(request) -> dict[str, object]:
    return {"theme": request.META["HTTP_X_THEME"]}


def _theme_processor(request) -> dict[str, object]:
    return {"theme": "dark"}


def _processors(*names: str) -> list[dict]:
    return [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {"context_processors": [f"{__name__}.{n}" for n in names]},
        }
    ]


async def _rows() -> list[int]:
    return [1]


@pytest.fixture(params=["sync", "async"])
def processed_page(request, page_instance, tmp_path) -> tuple[Page, Path]:
    """Return a page whose context builds on the sync path or through an async one."""
    page_file = tmp_path / "page.py"
    page_file.write_text('template = "ok"\n')
    if request.param == "async":
        page_instance.contexts.register_context(page_file, "rows", _rows)
    return page_instance, page_file


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


_FAILING_ASYNC = (
    "from next.pages import context\n\n"
    "@context('rows')\n"
    "async def rows():\n"
    "    raise LookupError\n\n"
    "template = '{{ rows }}'\n"
)


ROOT = 'metadata = {"title": {"template": "{title} | Root"}, "description": "Root"}\n'


PLAIN = "x = 1\n"


COUNTED = """
from next.deps import Depends
from next.pages import page


@page.context("seen")
def seen(counter=Depends("counter")):
    return counter


def render(request, counter=Depends("counter")):
    return "<p>body</p>{% metadata %}"


@page.metadata
def meta(seen, counter=Depends("counter")):
    return {"title": f"n{counter}/{seen}"}
"""


DYNAMIC = """
from next.pages import page

calls = []


@page.metadata
def meta():
    calls.append(1)
    return {"title": "Dynamic"}
"""


RESPONDING = """
from django.http import HttpResponse
from next.pages import page

calls = []


def render(request):
    return HttpResponse("short-circuit")


@page.metadata
def meta():
    calls.append(1)
    return {"title": "Dynamic"}
"""


GONE = """
from django.http import Http404
from next.pages import page


@page.metadata
def meta():
    raise Http404
"""


LEAF_CALLABLE = """
from next.deps import Depends
from next.pages import page

calls = []


@page.metadata
def leaf_meta({parameters}):
    calls.append(1)
    return {{"title": {title}}}
"""


def _leaf_callable(parameters: str = "", title: str = '"Own"') -> str:
    return LEAF_CALLABLE.format(parameters=parameters, title=title)


def _thunk(context_data: dict[str, object]) -> MetadataThunk:
    thunk = context_data[METADATA_KEY]
    assert isinstance(thunk, MetadataThunk)
    return thunk


def _counter() -> tuple[list[int], Any]:
    calls: list[int] = []

    def counter() -> int:
        calls.append(1)
        return len(calls)

    return calls, counter


class TestContextProcessorOutcomes:
    """A context processor merges a dict and nothing else, its failure logged."""

    def test_a_processor_returning_no_dict_adds_nothing(self, processed_page) -> None:
        instance, page_file = processed_page
        with override_settings(
            TEMPLATES=_processors("_silent_processor", "_theme_processor")
        ):
            context = instance.build_render_context(page_file, build_page_request())
        assert context["theme"] == "dark"
        assert None not in context

    def test_a_failing_processor_is_logged_and_skipped(
        self, processed_page, caplog
    ) -> None:
        instance, page_file = processed_page
        with override_settings(
            TEMPLATES=_processors("_failing_processor", "_theme_processor")
        ):
            context = instance.build_render_context(page_file, build_page_request())
        assert context["theme"] == "dark"
        assert "Error in context processor _failing_processor" in caplog.text

    def test_a_failing_processor_raises_under_strict_context(
        self, processed_page
    ) -> None:
        instance, page_file = processed_page
        with (
            override_settings(TEMPLATES=_processors("_failing_processor")),
            override_next_settings(STRICT_CONTEXT=True),
            pytest.raises(KeyError, match="HTTP_X_THEME"),
        ):
            instance.build_render_context(page_file, build_page_request())


class TestPageColour:
    """A page awaits when its run order or its metadata chain holds async code."""

    def test_a_sync_page_does_not_await_and_the_answer_is_memoised(
        self, tmp_path
    ) -> None:
        page_file = loaded_page(tmp_path, "template = 'ok'\n")

        assert page.awaits(page_file) is False
        with mock.patch.object(page.contexts, "awaits", side_effect=AssertionError):
            assert page.awaits(page_file) is False

    def test_an_async_context_makes_the_page_await(self, tmp_path) -> None:
        assert page.awaits(loaded_page(tmp_path, _ASYNC_CONTEXT)) is True

    def test_an_async_metadata_callable_makes_the_page_await(self, tmp_path) -> None:
        assert page.awaits(loaded_page(tmp_path, _ASYNC_METADATA)) is True

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

    def test_a_write_during_the_read_is_not_stamped_with_the_new_epoch(
        self, tmp_path
    ) -> None:
        instance = Page()
        page_file = tmp_path / "page.py"
        read = instance.contexts.awaits

        def racing(file_path: Path) -> bool:
            answer = read(file_path)
            instance.contexts.register_context(page_file, "late", _late)
            return answer

        with mock.patch.object(instance.contexts, "awaits", side_effect=racing):
            assert instance.awaits(page_file) is False
        assert instance.awaits(page_file) is True


class TestBuildRenderContext:
    """A render context of an async page is built through the bound driver."""

    def test_the_async_context_merges_with_the_processors(self, tmp_path) -> None:
        page_file = loaded_page(tmp_path, _ASYNC_CONTEXT)
        request = build_page_request()

        context = page.build_render_context(page_file, request)

        assert context["rows"] == [1, 2]
        assert context["total"] == 2
        assert context[REQUEST_KEY] is request
        assert context["request"] is request
        assert isinstance(context[METADATA_KEY], MetadataThunk)

    def test_a_render_without_a_request_runs_no_blocking_tail(self, tmp_path) -> None:
        page_file = loaded_page(
            tmp_path,
            "from next.pages import context\n\n"
            "@context('rows')\n"
            "async def rows():\n    return [1]\n\n"
            "template = 'ok'\n",
        )

        with mock.patch.object(
            manager_render, "run_segment", side_effect=AssertionError
        ):
            context = page.build_render_context(page_file)

        assert context["rows"] == [1]
        assert REQUEST_KEY not in context

    def test_async_metadata_folds_ahead_of_the_render(self, tmp_path) -> None:
        page_file = loaded_page(tmp_path, _ASYNC_METADATA)

        context = page.build_render_context(page_file, build_page_request())

        assert str(folded_metadata(context).folded().title) == "Async"

    def test_async_metadata_folds_without_a_request(self, tmp_path) -> None:
        page_file = loaded_page(tmp_path, _ASYNC_METADATA)

        context = page.build_render_context(page_file)

        assert str(folded_metadata(context).folded().title) == "Async"

    def test_a_failing_async_metadata_callable_is_left_out(self, tmp_path) -> None:
        page_file = loaded_page(tmp_path, _FAILING_METADATA)

        context = page.build_render_context(page_file, build_page_request())

        assert folded_metadata(context).folded().title is None

    def test_a_dict_below_an_async_ancestor_folds_in_order(self, tmp_path) -> None:
        loaded_page(
            tmp_path,
            "from next.pages import page\n\n"
            "@page.metadata(inherit=True)\n"
            "async def meta():\n"
            "    return {'title': 'Root', 'description': 'Root text'}\n\n"
            "template = 'ok'\n",
        )
        leaf = loaded_page(
            tmp_path, "metadata = {'description': 'Leaf text'}\ntemplate = 'ok'\n", "a"
        )

        context = page.build_render_context(leaf, build_page_request())

        assert folded_metadata(context).folded().description == "Leaf text"

    def test_a_refused_chain_keeps_the_lazy_thunk(self, tmp_path) -> None:
        page_file = loaded_page(
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


class TestGuardedContext:
    """An async page under a page boundary builds its context inside the guard."""

    def test_async_metadata_folds_once_the_guard_settled(self, tmp_path) -> None:
        (tmp_path / "error.djx").write_text("e")
        page_file = loaded_page(tmp_path, _ASYNC_METADATA)

        context = page.guarded_context(page_file, build_page_request())

        assert isinstance(context, dict)
        assert str(folded_metadata(context).folded().title) == "Async"
        assert SHELL_KEY in context

    def test_a_render_without_a_request_builds_on_the_loop(self, tmp_path) -> None:
        (tmp_path / "error.djx").write_text("e")
        page_file = loaded_page(tmp_path, _ASYNC_CONTEXT)

        context = page.guarded_context(page_file)

        assert isinstance(context, dict)
        assert context["total"] == 2

    def test_a_contained_failure_comes_back_with_its_shell(self, tmp_path) -> None:
        (tmp_path / "error.djx").write_text("e")
        page_file = loaded_page(tmp_path, _FAILING_ASYNC)

        caught = page.guarded_context(page_file, build_page_request())

        assert isinstance(caught, Caught)
        assert isinstance(caught.error, LookupError)
        assert caught.shell is not None
        assert REQUEST_KEY in caught.shell.tail


@pytest.fixture()
def cycle_collector_paused() -> Iterator[None]:
    """Stop the cycle collector for the test, so only reference counting frees objects."""
    gc.disable()
    yield
    gc.enable()


class TestRenderContext:
    """`build_render_context` seeds a thunk that reads the context it is handed."""

    def test_the_thunk_sees_the_finished_context(self, tmp_path: Path) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", _leaf_callable("late", "late"))])

        context_data = page.build_render_context(leaf)
        context_data["late"] = "Late"

        assert _thunk(context_data).fold(context_data).title == "Late"

    def test_a_static_chain_resolves_the_same_object_twice(
        self, tmp_path: Path
    ) -> None:
        instance = Page()
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        context_data = instance.build_render_context(leaf)
        thunk = _thunk(context_data)
        thunk.fold(context_data)
        first = thunk.fold(context_data)
        assert thunk.fold(context_data) is first
        assert first.description == "Root"

    @pytest.mark.usefixtures("cycle_collector_paused")
    def test_a_render_context_is_freed_without_the_cycle_collector(
        self, tmp_path: Path
    ) -> None:
        instance = Page()
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        request = build_page_request()
        context_data = instance.build_render_context(leaf, request)
        freed = weakref.ref(request)
        del context_data, request
        assert freed() is None

    def test_a_zone_batch_gets_the_thunk_too(self, tmp_path: Path) -> None:
        instance = Page()
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        context_data = instance.build_render_context(
            leaf, requested_zones=frozenset({"z"})
        )
        assert isinstance(context_data[METADATA_KEY], MetadataThunk)

    def test_url_kwargs_reach_the_callable(self, tmp_path: Path) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", _leaf_callable("slug", "slug"))])
        resolved = resolve_page_metadata(page, leaf, slug="wallet")
        assert isinstance(resolved, ResolvedMetadata)
        assert resolved.title == "wallet"

    def test_resolve_metadata_raises_what_the_callable_raises(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", GONE)])
        unified_view(page, leaf)
        with pytest.raises(Http404):
            resolve_page_metadata(page, leaf)


class TestOneDependencyCachePerRender:
    """`render()`, the context merge and the metadata callable share one cache."""

    def test_a_dependency_is_resolved_once_across_the_unified_view(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", COUNTED)])
        view = unified_view(page, leaf)
        calls, counter = _counter()
        with bound_dependency("counter", counter):
            response = view(build_page_request())

        assert response.status_code == 200
        assert calls == [1]
        assert "<title>n1/1</title>" in response.content.decode()

    def test_the_request_never_carries_the_cache_of_the_render(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", COUNTED)])
        request = build_page_request()
        calls, counter = _counter()
        with bound_dependency("counter", counter):
            unified_view(page, leaf)(request)
            page.build_render_context(leaf, request)
        assert next_of(request).dep_cache is None
        assert calls == [1, 1]

    def test_a_cache_already_on_the_request_is_reused(self, tmp_path: Path) -> None:
        source = _leaf_callable('wallet=Depends("wallet")', "wallet")
        (leaf,) = write_page_chain(tmp_path, [("leaf", source)])
        request = HttpRequest()
        next_of(request).dep_cache = {"wallet": "preloaded"}
        with bound_dependency("wallet", lambda: "fresh"):
            meta = resolve_page_metadata(page, leaf, request)
        assert meta.title == "preloaded"


class TestNothingRunsWithoutAReader:
    """A zone GET and a short-circuiting `render()` never run the callable."""

    def test_a_zone_get_never_runs_the_callable(self, tmp_path: Path) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", DYNAMIC)])
        (leaf.parent / "template.djx").write_text(
            '{% zone "z" %}<p>zoned</p>{% endzone %}'
        )
        view = unified_view(page, leaf)
        response = view(build_zone_request("z"))
        assert response.status_code == 200
        assert page_calls(leaf) == []

    def test_an_http_response_from_render_never_runs_the_callable(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", RESPONDING)])
        view = unified_view(page, leaf)
        response = view(build_page_request())
        assert response.content == b"short-circuit"
        assert page_calls(leaf) == []


class TestRenderContextPathPrecompute:
    """`build_render_context` reads the page path facts out of the memo."""

    def test_a_warm_context_build_probes_no_path(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """The second build resolves nothing and probes no sibling."""
        page_file = build_nested_page(tmp_path)
        page_instance.build_render_context(page_file, build_page_request())
        probes = record_path_calls(monkeypatch, "exists", path_under(tmp_path))
        resolves = record_path_calls(monkeypatch, "resolve", path_under(tmp_path))

        page_instance.build_render_context(page_file, build_page_request())

        assert probes == []
        assert resolves == []

    def test_the_context_paths_name_the_template_and_the_module(
        self, page_instance, tmp_path
    ) -> None:
        """The memo hands back the same two values the per-request probes did."""
        page_file = build_nested_page(tmp_path)

        context_data = page_instance.build_render_context(page_file)

        assert context_data["current_template_path"] == str(
            page_file.parent / "template.djx"
        )
        assert context_data["current_page_module_path"] == str(page_file.resolve())

    def test_register_template_refreshes_the_memoised_paths(
        self, page_instance, tmp_path
    ) -> None:
        """Writing the composed source drops the facts keyed off the same path."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        assert page_instance.build_render_context(page_file)[
            "current_template_path"
        ] == str(page_file)

        (tmp_path / "template.djx").write_text("<p>body</p>")
        page_instance.register_template(page_file, "<p>body</p>")

        assert page_instance.build_render_context(page_file)[
            "current_template_path"
        ] == str(tmp_path / "template.djx")

    def test_clear_template_caches_refreshes_the_memoised_paths(
        self, page_instance, tmp_path
    ) -> None:
        """The public drop takes the path facts with the composed layers."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        assert page_instance.build_render_context(page_file)[
            "current_template_path"
        ] == str(page_file)

        (tmp_path / "template.djx").write_text("<p>body</p>")
        page_instance.clear_template_caches()

        assert page_instance.build_render_context(page_file)[
            "current_template_path"
        ] == str(tmp_path / "template.djx")

    def test_a_created_template_djx_reaches_the_next_static_get(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A static page sees the sibling appear without a restart."""
        leaf = tmp_path / "leaf"
        leaf.mkdir()
        page_file = leaf / "page.py"
        page_file.write_text('template = "<p>{{ current_template_path }}</p>"')
        view = unified_view(page_instance, page_file)
        assert view(build_page_request()).content == f"<p>{page_file}</p>".encode()

        (leaf / "template.djx").write_text("<p>ignored</p>")
        touch_later(leaf)

        expected = f"<p>{leaf / 'template.djx'}</p>".encode()
        assert view(build_page_request()).content == expected

    def test_a_created_template_djx_reaches_the_next_dynamic_get(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A ``render()`` page sees it too, through the skeleton refresh."""
        leaf = tmp_path / "leaf"
        page_file = write_dynamic_page(
            leaf, returns="'<p>{{ current_template_path }}</p>'"
        )
        view = unified_view(page_instance, page_file)
        assert view(build_page_request()).content == f"<p>{page_file}</p>".encode()

        (leaf / "template.djx").write_text("<p>ignored</p>")
        touch_later(leaf)

        expected = f"<p>{leaf / 'template.djx'}</p>".encode()
        assert view(build_page_request()).content == expected
