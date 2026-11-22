import asyncio
from pathlib import Path
from unittest import mock

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import override_settings

from next.deps import REQUEST_DEP_CACHE_ATTR
from next.pages import page
from next.pages.loaders import (
    PageModuleImportError,
    load_page_module,
    load_python_module_memo,
)
from next.pages.manager import Built, render as manager_render
from tests.support import (
    bound_dependency,
    build_page_request,
    unified_view,
    write_page,
    write_page_chain,
)


_BROKEN_SYNTAX = "def render( invalid syntax {\n"


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


def _loaded(root: Path, source: str, trail: str = "") -> Path:
    """Write and import one `page.py`, so its registrations reach the global page."""
    page_file = write_page(root, trail, source)
    _module, error = load_page_module(page_file)
    assert error is None
    return page_file


GUARDED = """
from django.http import HttpResponse
from next.deps import Depends


def render(board=Depends("board")):
    return HttpResponse(board)
"""


class TestAuthorizationOutcomeBrokenPage:
    """`authorization_outcome` applies the view's guard to out-of-band morphs."""

    def _broken_file(self, tmp_path) -> Path:
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(_BROKEN_SYNTAX)
        return page_file

    def test_raises_under_debug(self, page_instance, tmp_path) -> None:
        page_file = self._broken_file(tmp_path)
        with (
            override_settings(DEBUG=True),
            pytest.raises(PageModuleImportError, match="failed to import"),
        ):
            page_instance.authorization_outcome(
                page_file, build_page_request(), "/sub/"
            )

    def test_raises_under_strict_loading(self, page_instance, tmp_path) -> None:
        page_file = self._broken_file(tmp_path)
        with (
            override_settings(NEXT_FRAMEWORK={"STRICT_LOADING": True}),
            pytest.raises(PageModuleImportError, match="failed to import"),
        ):
            page_instance.authorization_outcome(
                page_file, build_page_request(), "/sub/"
            )

    def test_raises_in_prod_instead_of_404(self, page_instance, tmp_path) -> None:
        page_file = self._broken_file(tmp_path)
        with pytest.raises(PageModuleImportError, match="failed to import"):
            page_instance.authorization_outcome(
                page_file, build_page_request(), "/sub/"
            )


class TestAuthorizationOutcomeVirtualPage:
    """`authorization_outcome` on a page whose body comes from `template.djx` alone."""

    def test_a_page_without_a_module_resolves_its_static_body(
        self, page_instance, tmp_path
    ) -> None:
        """A `template.djx`-only page has no module to carry a guard or a render."""
        (tmp_path / "template.djx").write_text("<p>virtual</p>")

        response, dynamic = page_instance.authorization_outcome(
            tmp_path / "page.py", build_page_request(), "/virtual/"
        )

        assert response is None
        assert dynamic is False

    def test_a_page_without_render_never_loads_its_body(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """A page with no guard of its own loads no body to authorize."""
        (tmp_path / "template.djx").write_text("<p>virtual</p>")
        calls: list[Path] = []

        def _record(file_path: Path, module: object) -> str:
            calls.append(file_path)
            return ""

        monkeypatch.setattr(page_instance, "_load_static_body", _record)

        page_instance.authorization_outcome(
            tmp_path / "page.py", build_page_request(), "/virtual/"
        )

        assert calls == []


class TestResolvePageBodyWithoutRender:
    """`resolve_page_body` falls to the static body for a module with no render."""

    def test_module_without_render_resolves_its_static_body(
        self, page_instance, tmp_path
    ) -> None:
        """A page.py carrying only a template attribute has no per-request body."""
        page_file = tmp_path / "page.py"
        page_file.write_text('template = "<p>static</p>"')

        module = load_python_module_memo(page_file)
        resolution = page_instance.resolve_page_body(page_file, module, dep_cache={})

        assert resolution.body == "<p>static</p>"
        assert resolution.http_response is None
        assert resolution.dynamic is False

    def test_a_page_without_a_module_resolves_its_loader_body(
        self, page_instance, tmp_path
    ) -> None:
        """A `template.djx`-only page has no module to carry a `render()`."""
        (tmp_path / "template.djx").write_text("<p>virtual</p>")

        resolution = page_instance.resolve_page_body(
            tmp_path / "page.py", None, dep_cache={}
        )

        assert resolution.body == "<p>virtual</p>"
        assert resolution.dynamic is False


class TestLoadStaticBodyEdgeCases:
    """`Page._load_static_body` edge cases."""

    def test_unreadable_template_djx_returns_empty(
        self, page_instance, tmp_path
    ) -> None:
        """UnicodeDecodeError on `template.djx` yields an empty body, not a crash."""
        template_djx = tmp_path / "template.djx"
        template_djx.write_bytes(b"\xff\xfe invalid utf-8")
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        assert page_instance._load_static_body(page_file, None) == ""


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
            manager_render, "bridged_scope", wraps=manager_render.bridged_scope
        ) as bridged:
            html = page.render(page_file, build_page_request())

        assert html == "2"
        assert bridged.call_count == 1


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
