import os
import time
from pathlib import Path
from unittest import mock

import pytest
from django.core.exceptions import PermissionDenied
from django.test import override_settings

from next.pages import Page
from next.pages.loaders import _load_python_module_memo
from next.pages.manager import template_edits_watched
from next.testing import envelope_of
from tests.support import (
    build_nested_page,
    build_page_request,
    build_zone_request,
    path_under,
    record_path_calls,
    unified_view,
)


_ZONED_BODY = 'a {% zone "z" %}<p>zoned</p>{% endzone %} b'
_ZONE_HTML = '<div data-next-zone="z"><p>zoned</p></div>'


def _is_djx(path: Path) -> bool:
    """Match a template source, the kind of file a staleness check stats."""
    return path.suffix == ".djx"


def _build_dynamic_page(directory: Path, *, returns: str = "'<p>dynamic</p>'") -> Path:
    """Write a ``page.py`` whose ``render()`` returns `returns` on every request."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(f"def render(request):\n    return {returns}\n")
    return page_file


class TestTemplateSourceSnapshot:
    """The mtime snapshot the composition caches compare themselves against."""

    def test_record_template_source_mtimes_empty_paths(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A page with no source at all records no snapshot to compare against."""
        page_file = tmp_path / "page.py"
        with mock.patch.object(
            page_instance, "_get_template_source_paths", return_value=[]
        ):
            page_instance._record_template_source_mtimes(
                page_file, page_instance._template_source_mtimes
            )
        assert page_file not in page_instance._template_source_mtimes

    def test_record_template_source_mtimes_skips_unstatable_sources(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """Sources that vanish between the walk and the stat leave no snapshot."""
        page_file = tmp_path / "page.py"
        with mock.patch.object(
            page_instance,
            "_get_template_source_paths",
            return_value=[tmp_path / "gone.djx"],
        ):
            page_instance._record_template_source_mtimes(
                page_file, page_instance._template_source_mtimes
            )
        assert page_file not in page_instance._template_source_mtimes

    def test_record_template_source_mtimes_snapshots_walked_dirs(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """The snapshot covers the directories the layout walk visits."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        page_instance._record_template_source_mtimes(
            page_file, page_instance._template_source_mtimes
        )
        assert tmp_path in page_instance._template_source_mtimes[page_file]

    def test_is_template_stale_reads_a_vanished_source_as_stale(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A tracked source that no longer stats counts as a change."""
        page_file = tmp_path / "page.py"
        missing_path = tmp_path / "removed.djx"
        page_instance._template_source_mtimes[page_file] = {missing_path: 1000.0}
        assert (
            page_instance._is_template_stale(
                page_file, page_instance._template_source_mtimes
            )
            is True
        )

    def test_is_template_stale_is_false_without_a_snapshot(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A path the store never saw is never stale."""
        page_file = tmp_path / "page.py"
        assert (
            page_instance._is_template_stale(
                page_file, page_instance._template_source_mtimes
            )
            is False
        )


class TestComposedTemplateCache:
    """`composed_template_for` caches the compiled composed template by mtime."""

    def test_render_twice_reuses_compiled_template(
        self, page_instance, tmp_path
    ) -> None:
        """A warm render reuses the compiled Template object as-is."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("<h1>{{ title }}</h1>")
        page_instance.render(page_file, title="One")
        compiled = page_instance._compiled_registry[page_file]
        result = page_instance.render(page_file, title="Two")
        assert page_instance._compiled_registry[page_file] is compiled
        assert "<h1>Two</h1>" in result

    def test_stale_source_recompiles(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """An edited template.djx invalidates both the source and compiled caches."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        djx = tmp_path / "template.djx"
        djx.write_text("<h1>{{ title }}</h1>")
        page_instance.render(page_file, title="One")
        compiled = page_instance._compiled_registry[page_file]
        djx.write_text("<h2>{{ title }}</h2>")
        result = page_instance.render(page_file, title="Two")
        assert page_instance._compiled_registry[page_file] is not compiled
        assert "<h2>Two</h2>" in result

    def test_stale_layout_recompiles(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """An edited ancestor layout.djx invalidates the compiled cache too."""
        layout = tmp_path / "layout.djx"
        layout.write_text("<html>{% block template %}{% endblock template %}</html>")
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text("x = 1")
        (page_dir / "template.djx").write_text("<p>body</p>")
        assert "<html>" in page_instance.render(page_file)
        layout.write_text("<main>{% block template %}{% endblock template %}</main>")
        assert "<main>" in page_instance.render(page_file)

    def test_register_template_drops_compiled_entry(
        self, page_instance, tmp_path
    ) -> None:
        """Every source-registry write evicts the compiled entry alongside."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("<h1>old</h1>")
        page_instance.render(page_file)
        assert page_file in page_instance._compiled_registry
        page_instance.register_template(page_file, "<p>replaced</p>")
        assert page_file not in page_instance._compiled_registry
        template = page_instance.composed_template_for(page_file)
        assert template.source == "<p>replaced</p>"

    def test_composed_template_carries_page_origin(
        self, page_instance, tmp_path
    ) -> None:
        """The compiled composed template names the page path as its origin."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("<p>ok</p>")
        template = page_instance.composed_template_for(page_file)
        assert template.origin.name == str(page_file)
        assert template.name == str(page_file)

    def test_render_function_pages_bypass_compiled_cache(
        self, page_instance, tmp_path
    ) -> None:
        """Dynamic `render()` bodies never populate the compiled cache."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "def render(request, **kwargs):\n    return '<p>dynamic</p>'\n"
        )
        response = unified_view(page_instance, page_file)(build_page_request())
        assert b"dynamic" in response.content
        assert page_file not in page_instance._compiled_registry


class TestStaticFastPathView:
    """A page without ``render()`` serves the cached composed template."""

    def test_warm_get_opens_no_template_file(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """The second GET reads nothing off disk and reuses the compiled template."""
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)

        view(build_page_request(), title="One")
        compiled = page_instance._compiled_registry[page_file]
        reads = record_path_calls(monkeypatch, "read_text", path_under(tmp_path))
        view(build_page_request(), title="Two")

        assert reads == []
        assert page_instance._compiled_registry[page_file] is compiled

    def test_html_matches_the_body_resolution_path(
        self, page_instance, tmp_path
    ) -> None:
        """The fast path composes the same HTML the per-request path composes."""
        page_file = build_nested_page(tmp_path)
        module = _load_python_module_memo(page_file)
        view = unified_view(page_instance, page_file)
        request = build_page_request()

        fast = view(request, title="Hi").content.decode()

        reference = Page()
        body = reference._load_static_body(page_file, module)
        assert fast == reference._render_composed(page_file, body, request, title="Hi")
        assert fast == "<html><main><h1>Hi</h1></main></html>"

    def test_created_layout_shows_up_without_a_restart(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A ``layout.djx`` created next to the page wraps the next GET."""
        page_file = build_nested_page(tmp_path, body="<h1>body</h1>")
        view = unified_view(page_instance, page_file)
        assert (
            view(build_page_request()).content
            == b"<html><main><h1>body</h1></main></html>"
        )

        (page_file.parent / "layout.djx").write_text(
            "<article>{% block template %}{% endblock template %}</article>"
        )

        assert b"<article>" in view(build_page_request()).content

    def test_deleted_layout_disappears_without_a_restart(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """An ancestor ``layout.djx`` deleted on disk leaves the next composition."""
        page_file = build_nested_page(tmp_path, body="<h1>body</h1>")
        view = unified_view(page_instance, page_file)
        assert b"<main>" in view(build_page_request()).content

        (page_file.parent.parent / "layout.djx").unlink()

        assert view(build_page_request()).content == b"<html><h1>body</h1></html>"

    def test_page_without_a_module_takes_the_static_branch(
        self, page_instance, tmp_path
    ) -> None:
        """A template-only page with no ``page.py`` serves from the cache too."""
        page_file = tmp_path / "page.py"
        (tmp_path / "template.djx").write_text("<p>virtual</p>")

        view = page_instance._create_unified_view(page_file, {}, None)
        assert view(build_page_request()).content == b"<p>virtual</p>"
        assert page_file in page_instance._template_registry

    def test_recompose_reads_the_current_template_attribute(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A recomposed page reads `page.py` as it stands, not as it was built."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )
        leaf = tmp_path / "leaf"
        leaf.mkdir()
        page_file = leaf / "page.py"
        page_file.write_text('template = "<p>first</p>"')
        view = unified_view(page_instance, page_file)
        assert b"first" in view(build_page_request()).content

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text('template = "<p>second</p>"')
        os.utime(page_file, (stamp, stamp))
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )

        assert b"second" in view(build_page_request()).content

    def test_template_loaded_fires_on_warm_up_only(
        self, page_instance, tmp_path, capture_template_loaded
    ) -> None:
        """The signal reports the composition, so a warm hit stays silent."""
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)

        view(build_page_request())
        assert [event["file_path"] for event in capture_template_loaded] == [page_file]

        view(build_page_request())
        assert len(capture_template_loaded) == 1


class TestLayoutSkeletonCache:
    """A ``render()`` page caches its layout chain, never its body."""

    def test_dynamic_body_never_reaches_the_template_registry(
        self, page_instance, tmp_path
    ) -> None:
        """The cached skeleton holds a slot where the per-request body goes."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )
        page_file = _build_dynamic_page(tmp_path / "leaf")

        response = unified_view(page_instance, page_file)(build_page_request())

        assert response.content == b"<html><p>dynamic</p></html>"
        assert page_file not in page_instance._template_registry
        assert "dynamic" not in page_instance._skeleton_registry[page_file]

    def test_second_request_walks_the_layout_chain_once(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """The skeleton is composed once and filled per request."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )
        page_file = _build_dynamic_page(tmp_path / "leaf")
        view = unified_view(page_instance, page_file)

        view(build_page_request())
        reads = record_path_calls(monkeypatch, "read_text", path_under(tmp_path))
        assert view(build_page_request()).content == b"<html><p>dynamic</p></html>"

        assert reads == []

    def test_created_layout_invalidates_the_skeleton(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A ``layout.djx`` created next to the page wraps the next dynamic GET."""
        page_file = _build_dynamic_page(tmp_path / "leaf")
        view = unified_view(page_instance, page_file)
        assert view(build_page_request()).content == b"<p>dynamic</p>"

        (page_file.parent / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )

        assert view(build_page_request()).content == b"<html><p>dynamic</p></html>"

    def test_deleted_layout_invalidates_the_skeleton(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A deleted ``layout.djx`` drops out of the next dynamic GET."""
        (tmp_path / "layout.djx").write_text(
            "<html>{% block template %}{% endblock template %}</html>"
        )
        page_file = _build_dynamic_page(tmp_path / "leaf")
        view = unified_view(page_instance, page_file)
        assert view(build_page_request()).content == b"<html><p>dynamic</p></html>"

        (tmp_path / "layout.djx").unlink()

        assert view(build_page_request()).content == b"<p>dynamic</p>"


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

        expected = f"<p>{leaf / 'template.djx'}</p>".encode()
        assert view(build_page_request()).content == expected

    def test_a_created_template_djx_reaches_the_next_dynamic_get(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """A ``render()`` page sees it too, through the skeleton refresh."""
        leaf = tmp_path / "leaf"
        page_file = _build_dynamic_page(
            leaf, returns="'<p>{{ current_template_path }}</p>'"
        )
        view = unified_view(page_instance, page_file)
        assert view(build_page_request()).content == f"<p>{page_file}</p>".encode()

        (leaf / "template.djx").write_text("<p>ignored</p>")

        expected = f"<p>{leaf / 'template.djx'}</p>".encode()
        assert view(build_page_request()).content == expected


class TestZoneTickOnTheStaticBranch:
    """A zone GET of a page without ``render()`` resolves no body of its own."""

    def test_the_first_tick_reads_the_body_once(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """Only the composition opens ``template.djx``, nothing reads it twice."""
        page_file = build_nested_page(tmp_path, body=_ZONED_BODY)
        view = unified_view(page_instance, page_file)
        reads = record_path_calls(monkeypatch, "read_text", path_under(tmp_path))

        response = view(build_zone_request("z"))

        assert response.status_code == 200
        assert reads.count(page_file.parent / "template.djx") == 1

    def test_a_warm_tick_opens_no_file(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """The second tick serves the cached composition without a read."""
        page_file = build_nested_page(tmp_path, body=_ZONED_BODY)
        view = unified_view(page_instance, page_file)
        view(build_zone_request("z"))
        reads = record_path_calls(monkeypatch, "read_text", path_under(tmp_path))

        response = view(build_zone_request("z"))

        assert reads == []
        assert envelope_of(response).html_for_zone("z") == _ZONE_HTML

    def test_a_tick_never_enters_body_resolution(self, page_instance, tmp_path) -> None:
        """Nothing about a static body depends on the request, so it is not resolved."""
        page_file = build_nested_page(tmp_path, body=_ZONED_BODY)
        view = unified_view(page_instance, page_file)

        with mock.patch.object(page_instance, "_resolve_page_body") as resolve:
            response = view(build_zone_request("z"))

        assert resolve.call_count == 0
        assert response.status_code == 200
        assert envelope_of(response).html_for_zone("z") == _ZONE_HTML
        assert b"a <" not in response.content


class TestZoneTickOnTheResolvingBranch:
    """A ``render()`` page keeps its per-request semantics on a zone tick."""

    def test_render_runs_once_per_tick(self, page_instance, tmp_path) -> None:
        """The body resolution the 400 rests on happens exactly once."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "calls = 0\n\n\ndef render(request):\n"
            "    global calls\n"
            "    calls += 1\n"
            "    return '<p>dynamic</p>'\n"
        )
        module = _load_python_module_memo(page_file)

        response = unified_view(page_instance, page_file)(build_zone_request("z"))

        assert response.status_code == 400
        assert module.calls == 1

    def test_a_zone_in_a_dynamic_body_is_a_bad_request(
        self, page_instance, tmp_path
    ) -> None:
        """A body built per request has no compiled source to render a zone from."""
        page_file = _build_dynamic_page(
            tmp_path, returns="'{% zone \"z\" %}x{% endzone %}'"
        )

        response = unified_view(page_instance, page_file)(build_zone_request("z"))

        assert response.status_code == 400
        assert response.content == b"zone in dynamic body"

    def test_a_render_redirect_short_circuits_the_tick(
        self, page_instance, tmp_path
    ) -> None:
        """A redirect answers the tick before the shaper is consulted."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from django.http import HttpResponseRedirect\n\n\n"
            "def render(request):\n    return HttpResponseRedirect('/login/')\n"
        )

        response = unified_view(page_instance, page_file)(build_zone_request("z"))

        assert response.status_code == 302
        assert response["Location"] == "/login/"

    def test_a_render_denial_short_circuits_the_tick(
        self, page_instance, tmp_path
    ) -> None:
        """A guard raising inside ``render()`` reaches the tick untouched."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from django.core.exceptions import PermissionDenied\n\n\n"
            "def render(request):\n    raise PermissionDenied\n"
        )
        view = unified_view(page_instance, page_file)

        with pytest.raises(PermissionDenied):
            view(build_zone_request("z"))


class TestTemplateStalenessGate:
    """Source staleness is stat-checked only where template edits are watched."""

    def test_the_gate_follows_the_debug_setting(self, watched_template_edits) -> None:
        """The predicate is read per call, so an override takes effect at once."""
        assert template_edits_watched() is True
        with override_settings(DEBUG=False):
            assert template_edits_watched() is False

    def test_a_production_process_still_records_a_snapshot(
        self, page_instance, tmp_path
    ) -> None:
        """The snapshot is taken without the watch, so a later watch can compare."""
        page_file = build_nested_page(tmp_path)

        page_instance.render(page_file, title="One")

        assert page_file in page_instance._template_source_mtimes

    def test_a_watch_turned_on_later_sees_an_edit(
        self, page_instance, tmp_path
    ) -> None:
        """An entry composed with the watch off goes stale once the watch comes on."""
        page_file = build_nested_page(tmp_path)
        template_file = page_file.parent / "template.djx"
        page_instance.render(page_file, title="One")

        template_file.write_text("TWO")
        os.utime(template_file, (time.time() + 1, time.time() + 1))
        with override_settings(DEBUG=True):
            rendered = page_instance.render(page_file, title="One")

        assert "TWO" in rendered

    def test_a_dev_process_snapshots_its_sources(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """The dev loop keeps the snapshot the staleness check reads."""
        page_file = build_nested_page(tmp_path)

        page_instance.render(page_file, title="One")

        assert page_file in page_instance._template_source_mtimes

    def test_a_warm_get_stats_no_source_in_production(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """The production hit answers from the caches without touching a source."""
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)
        view(build_page_request(), title="One")
        stats = record_path_calls(monkeypatch, "stat", _is_djx)

        view(build_page_request(), title="Two")

        assert stats == []

    def test_a_warm_get_stats_its_sources_in_dev(
        self, page_instance, tmp_path, monkeypatch, watched_template_edits
    ) -> None:
        """The dev hit pays the stat that makes an edit visible."""
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)
        view(build_page_request(), title="One")
        stats = record_path_calls(monkeypatch, "stat", _is_djx)

        view(build_page_request(), title="Two")

        assert (page_file.parent / "template.djx") in stats

    def test_a_warm_zone_tick_stats_no_source_in_production(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """The poll tick production serves carries no staleness bookkeeping."""
        page_file = build_nested_page(tmp_path, body=_ZONED_BODY)
        view = unified_view(page_instance, page_file)
        view(build_zone_request("z"))
        stats = record_path_calls(monkeypatch, "stat", _is_djx)

        response = view(build_zone_request("z"))

        assert stats == []
        assert envelope_of(response).html_for_zone("z") == _ZONE_HTML

    def test_an_edited_layout_reaches_the_next_zone_tick_in_dev(
        self, page_instance, tmp_path, watched_template_edits
    ) -> None:
        """The dev loop of a zone declared in an ancestor layout stays intact."""
        (tmp_path / "layout.djx").write_text(
            '<html>{% zone "z" %}<p>first</p>{% endzone %}'
            "{% block template %}{% endblock template %}</html>"
        )
        leaf = tmp_path / "leaf"
        leaf.mkdir()
        page_file = leaf / "page.py"
        page_file.write_text("x = 1")
        (leaf / "template.djx").write_text("<p>body</p>")
        view = unified_view(page_instance, page_file)
        assert b"first" in view(build_zone_request("z")).content

        (tmp_path / "layout.djx").write_text(
            '<html>{% zone "z" %}<p>second</p>{% endzone %}'
            "{% block template %}{% endblock template %}</html>"
        )

        assert b"second" in view(build_zone_request("z")).content

    def test_production_serves_the_composed_template_until_the_caches_drop(
        self, page_instance, tmp_path
    ) -> None:
        """An edit lands on the next deploy, or on an explicit cache drop."""
        page_file = build_nested_page(tmp_path, body="<h1>first</h1>")
        view = unified_view(page_instance, page_file)
        assert b"first" in view(build_page_request()).content

        (page_file.parent / "template.djx").write_text("<h1>second</h1>")
        assert b"first" in view(build_page_request()).content

        page_instance.clear_template_caches()

        assert b"second" in view(build_page_request()).content
