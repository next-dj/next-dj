import os
from pathlib import Path
from typing import Any

import pytest
from django.http import Http404, HttpRequest

from next.checks import reset_check_caches
from next.deps import REQUEST_DEP_CACHE_ATTR, Depends
from next.pages import Page, page
from next.pages.loaders import _load_python_module_memo
from next.pages.manager import reset_metadata_registry
from next.pages.metadata import MetadataThunk
from next.seeding import METADATA_KEY
from tests.support import (
    attribution,
    bound_dependency,
    build_page_request,
    build_zone_request,
    handler_declared_here,
    unified_view,
    write_page_chain,
)


ROOT = 'metadata = {"title": {"template": "{title} | Root"}, "description": "Root"}\n'
PLAIN = "x = 1\n"
COUNTED = """
from next.deps import Depends
from next.pages import page
from next.pages.metadata import Metadata


@page.context("seen")
def seen(counter=Depends("counter")):
    return counter


def render(request, counter=Depends("counter")):
    return "<p>body</p>"


@page.metadata
def meta(parent: Metadata, seen, counter=Depends("counter")):
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


class TestDecorator:
    """`Page.metadata` registers the callable under the file that declared it."""

    def test_bare_spelling_registers_without_inherit(self) -> None:
        instance = Page()

        @instance.metadata
        def meta() -> dict[str, str]:
            return {}

        entry = instance._metadata_registry.entry(Path(__file__))
        assert entry is not None
        assert entry.func is meta
        assert entry.inherit is False

    def test_called_spelling_carries_inherit(self) -> None:
        instance = Page()

        @instance.metadata(inherit=True)
        def meta() -> dict[str, str]:
            return {}

        entry = instance._metadata_registry.entry(Path(__file__))
        assert entry is not None
        assert entry.inherit is True

    def test_the_decorator_hands_the_callable_back(self) -> None:
        instance = Page()

        def meta() -> dict[str, str]:
            return {}

        assert instance.metadata(meta) is meta
        assert instance.metadata()(meta) is meta

    def test_a_local_callable_is_no_misattribution(self) -> None:
        instance = Page()

        @instance.metadata
        def meta() -> dict[str, str]:
            return {}

        assert instance._metadata_registry.misattributed() == ()

    def test_a_helper_from_another_module_is_recorded(self) -> None:
        instance = Page()
        instance.metadata(handler_declared_here)
        records = instance._metadata_registry.misattributed()
        assert [(r.registered_from, r.declared_in, r.name) for r in records] == [
            (Path(__file__), Path(attribution.__file__), "handler_declared_here")
        ]

    def test_two_names_in_one_file_are_a_conflict(self) -> None:
        instance = Page()

        @instance.metadata
        def first() -> dict[str, str]:
            return {}

        @instance.metadata
        def second() -> dict[str, str]:
            return {}

        assert instance._metadata_registry.conflicts() == {
            Path(__file__): ("first", "second")
        }

    def test_metadata_names_lists_the_callable_per_file(self) -> None:
        instance = Page()

        @instance.metadata
        def meta() -> dict[str, str]:
            return {}

        assert instance.metadata_names() == {Path(__file__): ("meta",)}


class TestStaticReads:
    """The request-free reads go through the registry of the instance."""

    def test_static_metadata_folds_the_chain(self, tmp_path: Path) -> None:
        instance = Page()
        _root, leaf = write_page_chain(
            tmp_path, [("root", ROOT), ("leaf", 'metadata = {"title": "Leaf"}\n')]
        )
        meta = instance.static_metadata(leaf)
        assert str(meta.title) == "Leaf | Root"
        assert meta.description == "Root"

    def test_templated_title_applies_the_chain_template(self, tmp_path: Path) -> None:
        instance = Page()
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        assert str(instance.templated_title(leaf, "Post")) == "Post | Root"
        assert instance.templated_title(leaf, "Post", absolute=True) == "Post"


class TestRenderContext:
    """`build_render_context` seeds a thunk over the very dict it returns."""

    def test_the_thunk_sees_the_finished_context(self, tmp_path: Path) -> None:
        instance = Page()
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        instance._metadata_registry.register(leaf, lambda late: {"title": late})

        context_data = instance.build_render_context(leaf)
        context_data["late"] = "Late"

        assert _thunk(context_data).resolve().title == "Late"

    def test_the_thunk_resolves_the_same_object_twice(self, tmp_path: Path) -> None:
        instance = Page()
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        thunk = _thunk(instance.build_render_context(leaf))
        first = thunk.resolve()
        assert thunk.resolve() is first
        assert first.description == "Root"

    def test_a_zone_batch_gets_the_thunk_too(self, tmp_path: Path) -> None:
        instance = Page()
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        context_data = instance.build_render_context(
            leaf, _requested_zones=frozenset({"z"})
        )
        assert isinstance(context_data[METADATA_KEY], MetadataThunk)

    def test_url_kwargs_reach_the_callable(self, tmp_path: Path) -> None:
        instance = Page()
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        instance._metadata_registry.register(leaf, lambda slug: {"title": slug})
        assert instance.resolve_metadata(leaf, slug="wallet").title == "wallet"

    def test_resolve_metadata_raises_what_the_callable_raises(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", GONE)])
        unified_view(page, leaf)
        with pytest.raises(Http404):
            page.resolve_metadata(leaf)


class TestOneDependencyCachePerRequest:
    """`render()`, the context merge and the metadata callable share one cache."""

    def test_a_dependency_is_resolved_once_across_the_unified_view(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", COUNTED)])
        view = unified_view(page, leaf)
        built: list[dict[str, object]] = []
        original = page.build_render_context

        def capturing(*args: object, **kwargs: object) -> dict[str, object]:
            context_data = original(*args, **kwargs)
            built.append(context_data)
            return context_data

        monkeypatch.setattr(page, "build_render_context", capturing)
        calls, counter = _counter()
        with bound_dependency("counter", counter):
            response = view(build_page_request())
            meta = _thunk(built[0]).resolve()

        assert response.status_code == 200
        assert calls == [1]
        assert meta.title == "n1/1"

    def test_the_request_carries_the_cache_render_filled(self, tmp_path: Path) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", COUNTED)])
        request = build_page_request()
        module = _load_python_module_memo(leaf)
        assert module is not None
        calls, counter = _counter()
        with bound_dependency("counter", counter):
            page._call_render_function(module.render, leaf, request)
            cache = getattr(request, REQUEST_DEP_CACHE_ATTR)
            assert cache == {"counter": 1}
            page.build_render_context(leaf, request)
        assert calls == [1]

    def test_a_cache_already_on_the_request_is_reused(self, tmp_path: Path) -> None:
        instance = Page()
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        instance._metadata_registry.register(
            leaf, lambda wallet=Depends("wallet"): {"title": wallet}
        )
        request = HttpRequest()
        setattr(request, REQUEST_DEP_CACHE_ATTR, {"wallet": "preloaded"})
        with bound_dependency("wallet", lambda: "fresh"):
            meta = instance.resolve_metadata(leaf, request)
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
        module = _load_python_module_memo(leaf)
        assert module is not None
        assert module.calls == []

    def test_an_http_response_from_render_never_runs_the_callable(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", RESPONDING)])
        view = unified_view(page, leaf)
        response = view(build_page_request())
        assert response.content == b"short-circuit"
        module = _load_python_module_memo(leaf)
        assert module is not None
        assert module.calls == []


class TestReset:
    """The shared registry and its chains drop together on a reset."""

    def test_reset_metadata_registry_drops_registrations_and_chains(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        page._metadata_registry.register(leaf, lambda: {"title": "Dynamic"})
        page.resolve_metadata(leaf)
        assert leaf in page._metadata_registry._chains

        reset_metadata_registry()

        assert page._metadata_registry.entry(leaf) is None
        assert leaf not in page._metadata_registry._chains

    def test_a_removed_callable_is_gone_after_a_check_reset(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", DYNAMIC)])
        unified_view(page, leaf)
        assert page.resolve_metadata(leaf).title == "Dynamic"

        stamp = leaf.stat().st_mtime + 10
        leaf.write_text(PLAIN)
        os.utime(leaf, (stamp, stamp))
        reset_check_caches()

        assert page.resolve_metadata(leaf).title is None
