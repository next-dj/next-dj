import gc
import os
import weakref
from pathlib import Path
from typing import Any

import pytest
from django.http import Http404, HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.test import RequestFactory, override_settings

from next.checks import reset_check_caches
from next.deps import REQUEST_DEP_CACHE_ATTR, Depends, get_request_dep_cache
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
    record_calls,
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
GUARDED = """
from django.http import HttpResponse
from next.deps import Depends


def render(board=Depends("board")):
    return HttpResponse(board)
"""
GONE = """
from django.http import Http404
from next.pages import page


@page.metadata
def meta():
    raise Http404
"""

HEADED = Path(__file__).resolve().parent.parent / "site_pages" / "headed" / "page.py"
PAGED = {"METADATA": {"CANONICAL_QUERY": ("page",)}}


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


def _render(query: str = "") -> str:
    response = unified_view(page, HEADED)(RequestFactory().get(f"/headed/{query}"))
    assert isinstance(response, HttpResponse | TemplateResponse)
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture(scope="module")
def html() -> str:
    return _render()


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

    def test_an_absolute_title_runs_no_inherited_callable(self, tmp_path: Path) -> None:
        instance = Page()
        root, leaf = write_page_chain(tmp_path, [("root", PLAIN), ("leaf", PLAIN)])
        calls: list[int] = []

        def root_meta() -> dict[str, object]:
            calls.append(1)
            return {"title": {"template": "{title} | Dyn"}}

        instance._metadata_registry.register(root, root_meta, inherit=True)
        assert instance.templated_title(leaf, "Post", absolute=True) == "Post"
        assert calls == []


class TestRenderContext:
    """`build_render_context` seeds a thunk that reads the context it is handed."""

    def test_the_thunk_sees_the_finished_context(self, tmp_path: Path) -> None:
        instance = Page()
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        instance._metadata_registry.register(leaf, lambda late: {"title": late})

        context_data = instance.build_render_context(leaf)
        context_data["late"] = "Late"

        assert _thunk(context_data).resolve(context_data).title == "Late"

    def test_a_static_chain_resolves_the_same_object_twice(
        self, tmp_path: Path
    ) -> None:
        instance = Page()
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        context_data = instance.build_render_context(leaf)
        thunk = _thunk(context_data)
        thunk.resolve(context_data)
        first = thunk.resolve(context_data)
        assert thunk.resolve(context_data) is first
        assert first.description == "Root"

    def test_a_render_context_is_freed_without_the_cycle_collector(
        self, tmp_path: Path
    ) -> None:
        instance = Page()
        (leaf,) = write_page_chain(tmp_path, [("leaf", PLAIN)])
        request = build_page_request()
        context_data = instance.build_render_context(leaf, request)
        freed = weakref.ref(request)
        gc.disable()
        try:
            del context_data, request
            assert freed() is None
        finally:
            gc.enable()

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


class TestOneDependencyCachePerRender:
    """`render()`, the context merge and the metadata callable share one cache."""

    def test_a_dependency_is_resolved_once_across_the_unified_view(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", COUNTED)])
        view = unified_view(page, leaf)
        built = record_calls(monkeypatch, page, "_render_context")
        calls, counter = _counter()
        with bound_dependency("counter", counter):
            response = view(build_page_request())
            context_data = built[0].result
            meta = _thunk(context_data).resolve(context_data)

        assert response.status_code == 200
        assert calls == [1]
        assert meta.title == "n1/1"

    def test_the_request_never_carries_the_cache_of_the_render(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", COUNTED)])
        request = build_page_request()
        calls, counter = _counter()
        with bound_dependency("counter", counter):
            unified_view(page, leaf)(request)
            page.build_render_context(leaf, request)
        assert get_request_dep_cache(request) is None
        assert calls == [1, 1]

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


class TestAuthorizationResolvesLikeAVisit:
    """A foreign page's guard resolves its named dependencies for its own URL."""

    def test_the_dispatch_cache_of_the_caller_stays_out_of_the_guard(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("board", GUARDED)])
        unified_view(page, leaf)
        request = HttpRequest()
        request.method = "POST"
        setattr(request, REQUEST_DEP_CACHE_ATTR, {"board": "id=1"})
        with bound_dependency("board", lambda board_id: f"id={board_id}"):
            denial, dynamic = page.authorization_outcome(
                leaf, request, "/boards/2/", {"board_id": 2}
            )
        assert denial is not None
        assert denial.content == b"id=2"
        assert dynamic is False
        assert getattr(request, REQUEST_DEP_CACHE_ATTR) == {"board": "id=1"}

    def test_two_guards_on_one_request_resolve_apart(self, tmp_path: Path) -> None:
        (leaf,) = write_page_chain(tmp_path, [("board", GUARDED)])
        unified_view(page, leaf)
        request = build_page_request()
        with bound_dependency("board", lambda board_id: f"id={board_id}"):
            first, _ = page.authorization_outcome(
                leaf, request, "/b/1/", {"board_id": 1}
            )
            second, _ = page.authorization_outcome(
                leaf, request, "/b/2/", {"board_id": 2}
            )
        assert first is not None
        assert second is not None
        assert (first.content, second.content) == (b"id=1", b"id=2")


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
        assert page._metadata_registry.chain(leaf) is not None

        reset_metadata_registry()

        assert page._metadata_registry.entry(leaf) is None
        assert page._metadata_registry.chain(leaf) is None

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


class TestUnifiedViewRendersTheHead:
    """A layout's ``{% metadata %}`` renders the folded chain of the page it wraps."""

    def test_the_head_carries_the_page_metadata(self, html: str) -> None:
        assert html.startswith("<html><head><title>Headed</title>\n")
        assert "<h1>headed page</h1>" in html

    def test_the_description_is_escaped(self, html: str) -> None:
        assert "<script>alert(1)</script>" not in html
        assert 'content="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;">' in html

    def test_jsonld_never_closes_the_script(self, html: str) -> None:
        assert "</script><!--" not in html
        assert (
            '<script type="application/ld+json">{"@type": "WebPage", '
            '"name": "\\u003C/script\\u003E\\u003C!--"}</script>' in html
        )

    def test_og_is_derived_from_the_page(self, html: str) -> None:
        assert '<meta property="og:title" content="Headed">' in html
        assert '<meta property="og:url" content="https://acme.example/headed/">' in html
        assert '<meta property="og:type" content="website">' in html

    def test_the_canonical_is_the_self_url_under_the_base(self) -> None:
        assert '<link rel="canonical" href="https://acme.example/headed/">' in (
            _render("?utm=1&page=1")
        )

    @override_settings(NEXT_FRAMEWORK=PAGED)
    def test_every_render_resolves_the_canonical_of_its_own_query(self) -> None:
        second = _render("?utm=1&page=2")
        third = _render("?page=3")
        assert '<link rel="canonical" href="https://acme.example/headed/?page=2">' in (
            second
        )
        assert '<link rel="canonical" href="https://acme.example/headed/?page=3">' in (
            third
        )
