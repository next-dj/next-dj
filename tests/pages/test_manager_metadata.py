import gc
import weakref
from pathlib import Path
from typing import Any

import pytest
from django.http import Http404, HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.test import Client, RequestFactory, override_settings

from next.checks import reset_check_caches
from next.deps import REQUEST_DEP_CACHE_ATTR
from next.pages import Page, PageMetadataConflictError, page
from next.pages.loaders import load_page_module
from next.pages.manager import reset_metadata_registry
from next.pages.metadata import ResolvedMetadata
from next.pages.metadata.chain import MetadataThunk
from next.pages.metadata.markers import Segment, TitleSpec
from next.pages.metadata.registry import MetadataRegistrations
from next.seeding import METADATA_KEY
from next.testing import assert_metadata, override_next_settings
from tests.support import (
    WITH_BASE,
    attribution,
    bound_dependency,
    build_page_request,
    build_zone_request,
    handler_declared_here,
    resolve_page_metadata,
    routed,
    touch_later,
    unified_view,
    write_page,
    write_page_chain,
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

TWICE = (
    DYNAMIC
    + """

@page.metadata
def other():
    return {"title": "Other"}
"""
)

ROOT_CALLABLE = """
from next.pages import page


@page.metadata{spelling}
def meta():
    return {{"description": "Root"}}
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


def _calls(page_file: Path) -> list[int]:
    module, _error = load_page_module(page_file)
    assert module is not None
    return module.calls


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
    with override_next_settings(**WITH_BASE):
        response = unified_view(page, HEADED)(RequestFactory().get(f"/headed/{query}"))
    assert isinstance(response, HttpResponse | TemplateResponse)
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture(scope="module")
def html() -> str:
    return _render()


class TestDecorator:
    """`Page.metadata` registers the callable under the file that declared it."""

    @pytest.mark.parametrize(
        ("spelling", "inherited"),
        [("", None), ("()", None), ("(inherit=True)", "Root")],
        ids=["bare", "called", "inherit"],
    )
    def test_only_an_inherited_callable_runs_for_a_descendant(
        self, tmp_path: Path, spelling: str, inherited: str | None
    ) -> None:
        root, leaf = write_page_chain(
            tmp_path,
            [("root", ROOT_CALLABLE.format(spelling=spelling)), ("leaf", PLAIN)],
        )
        assert resolve_page_metadata(page, root).description == "Root"
        assert resolve_page_metadata(page, leaf).description == inherited

    def test_the_decorator_hands_the_callable_back(self) -> None:
        instance = Page()

        def meta() -> dict[str, str]:
            return {}

        assert instance.metadata(meta) is meta
        assert instance.metadata()(meta) is meta
        assert instance.metadata_registrations().names == {Path(__file__): ("meta",)}

    def test_a_local_callable_is_no_misattribution(self) -> None:
        instance = Page()

        @instance.metadata
        def meta() -> dict[str, str]:
            return {}

        assert instance.metadata_registrations().misattributed == ()

    def test_a_helper_from_another_module_is_recorded(self) -> None:
        instance = Page()
        instance.metadata(handler_declared_here)
        records = instance.metadata_registrations().misattributed
        assert [(r.registered_from, r.declared_in, r.name) for r in records] == [
            (Path(__file__), Path(attribution.__file__), "handler_declared_here")
        ]

    def test_two_callables_in_one_page_py_refuse_to_load(self, tmp_path: Path) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", TWICE)])
        module, error = load_page_module(leaf)
        assert module is None
        assert error is not None
        assert isinstance(error.__cause__, PageMetadataConflictError)
        assert "'meta' and 'other'" in str(error.__cause__)

    @override_settings(DEBUG=True)
    def test_an_edited_page_py_registers_again_without_a_conflict(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", DYNAMIC)])
        load_page_module(leaf)
        touch_later(leaf, DYNAMIC.replace("def meta", "def renamed"))
        module, error = load_page_module(leaf)
        assert module is not None
        assert error is None
        assert page.metadata_registrations().names == {leaf: ("renamed",)}


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

    def test_the_declaration_the_chain_and_the_registrations_read_through(
        self, tmp_path: Path
    ) -> None:
        root, leaf = write_page_chain(
            tmp_path, [("root", ROOT), ("leaf", _leaf_callable())]
        )
        assert page.metadata_declaration(root).raw == {
            "title": {"template": "{title} | Root"},
            "description": "Root",
        }
        declaration = page.metadata_declaration(leaf)
        assert declaration.raw is None
        assert declaration.entry is not None
        assert declaration.entry.inherit is False
        assert [s.file_path for s in page.metadata_chain(leaf).sources] == [root, leaf]
        assert page.metadata_registrations() == MetadataRegistrations(
            names={leaf: ("leaf_meta",)}, misattributed=()
        )

    def test_fold_metadata_lays_an_overlay_under_the_chain_template(
        self, tmp_path: Path
    ) -> None:
        instance = Page()
        _root, leaf = write_page_chain(tmp_path, [("root", ROOT), ("leaf", PLAIN)])
        overlay = Segment("overlay", title=TitleSpec(text="Post"))
        assert str(instance.fold_metadata(leaf, overlay=overlay).title) == (
            "Post | Root"
        )
        assert instance.fold_metadata(leaf).description == "Root"

    def test_an_absolute_overlay_runs_no_inherited_callable_of_its_own_page(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", _leaf_callable())])
        overlay = Segment("overlay", title=TitleSpec(absolute="Post"))
        assert page.fold_metadata(leaf, overlay=overlay).title == "Post"
        assert _calls(leaf) == []


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
        assert not hasattr(request, REQUEST_DEP_CACHE_ATTR)
        assert calls == [1, 1]

    def test_a_cache_already_on_the_request_is_reused(self, tmp_path: Path) -> None:
        source = _leaf_callable('wallet=Depends("wallet")', "wallet")
        (leaf,) = write_page_chain(tmp_path, [("leaf", source)])
        request = HttpRequest()
        setattr(request, REQUEST_DEP_CACHE_ATTR, {"wallet": "preloaded"})
        with bound_dependency("wallet", lambda: "fresh"):
            meta = resolve_page_metadata(page, leaf, request)
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
        assert _calls(leaf) == []

    def test_an_http_response_from_render_never_runs_the_callable(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", RESPONDING)])
        view = unified_view(page, leaf)
        response = view(build_page_request())
        assert response.content == b"short-circuit"
        assert _calls(leaf) == []


class TestReset:
    """The shared registry and its chains drop together on a reset."""

    def test_reset_metadata_registry_drops_registrations_and_chains(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", DYNAMIC)])
        chain = page.metadata_chain(leaf)
        assert page.metadata_chain(leaf) is chain
        assert page.metadata_registrations().names == {leaf: ("meta",)}

        reset_metadata_registry()

        assert page.metadata_registrations() == MetadataRegistrations({}, ())
        assert page.metadata_declaration(leaf).entry is None
        assert page.metadata_chain(leaf) is not chain

    def test_a_removed_callable_is_gone_after_a_check_reset(
        self, tmp_path: Path
    ) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", DYNAMIC)])
        unified_view(page, leaf)
        assert resolve_page_metadata(page, leaf).title == "Dynamic"

        touch_later(leaf, PLAIN)
        reset_check_caches()

        assert resolve_page_metadata(page, leaf).title is None


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
            '<script type="application/ld+json">{"@context": "https://schema.org", '
            '"@graph": [{"@type": "WebPage", "name": "\\u003C/script\\u003E\\u003C!--"}]}'
            "</script>" in html
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


HEAD = "<head>{% metadata %}</head>"


class TestTheHeadThroughARequest:
    """What a visitor receives follows the page tree on disk, braces and all."""

    @override_settings(DEBUG=True)
    def test_an_edited_ancestor_reaches_the_next_request_under_debug(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "pages"
        ancestor = write_page(root, "", ROOT, body=HEAD)
        write_page(root, "leaf", 'metadata = {"title": "Leaf"}\n', body=HEAD)
        with routed(root):
            first = Client().get("/leaf/")
            touch_later(ancestor, ROOT.replace("Root", "Edited"))
            second = Client().get("/leaf/")
        assert_metadata(first, title="Leaf | Root", description="Root")
        assert_metadata(second, title="Leaf | Edited", description="Edited")

    def test_braces_in_a_title_and_a_site_name_stay_literal(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "pages"
        write_page(
            root,
            "",
            'metadata = {"title": {"template": "{title} | {site_name}"}, '
            '"site_name": "Acme {Co}"}\n',
            body=HEAD,
        )
        write_page(
            root, "deals", 'metadata = {"title": "Deals {50%} {title}"}\n', body=HEAD
        )
        with routed(root):
            response = Client().get("/deals/")
        assert response.status_code == 200
        assert_metadata(response, title="Deals {50%} {title} | Acme {Co}")
