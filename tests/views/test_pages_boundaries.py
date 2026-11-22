import re
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth.models import Group
from django.http import HttpRequest, HttpResponse
from django.test import Client, override_settings

import next.pages.manager as manager_module
from next.boundaries import boundary_failed, render_boundaries
from next.pages import page
from next.pages.loaders import load_python_module_memo
from next.pages.manager import bodies as manager_bodies, render as manager_render
from next.pages.signals import page_rendered
from next.testing import capture_signals, envelope_of
from tests.support import (
    build_page_request,
    build_zone_request,
    routed,
    touch_later,
    unified_view,
    write_page,
)
from tests.support.access import COMPONENTS
from tests.support.components import write_component


_EXPLODING = """
from next.pages import context


def _explode():
    raise ValueError("body")


@context("boom")
def boom():
    return _explode
"""

_FAILING_CONTEXT = """
from next.pages import context


@context("value")
def value():
    raise ValueError("context")
"""

_FAILING_INHERITED = """
from next.pages import context

template = "<p>sec</p>"


@context("shared", inherit_context=True)
def shared():
    raise ValueError("inherited")
"""

_FAILING_ASYNC_CONTEXT = """
from next.pages import context


@context("value")
async def value():
    raise ValueError("async context")


async def render():
    return "<h1>{{ value }}</h1>"
"""

_WORKING_ASYNC = """
from next.pages import context


@context("value")
async def value():
    return "fine"


async def render():
    return "<h1>{{ value }}</h1>"
"""

_FAILING_WIDGET = """
from next.components import component


@component.context("value")
def value():
    raise RuntimeError("widget down")
"""

_RAISES_404 = """
from django.http import Http404


def render():
    raise Http404
"""

_RAISES_403 = """
from django.core.exceptions import PermissionDenied


async def render():
    raise PermissionDenied
"""

_RAISES_400 = """
from django.core.exceptions import BadRequest
from next.pages import context

template = "{{ value }}"


@context("value")
def value():
    raise BadRequest
"""

_FAILING_RENDER = "def render():\n    raise ValueError('render')\n"
_SEC_ERROR = "<p>sec {{ error.kind }}</p>"
_BROKEN_ERROR = '{% include "next-missing.djx" %}'
_ROOT_ERROR = "<p>root {{ error.kind }}</p>"
_SEC_CAUGHT = (
    '<html><section><div id="b-x" data-next-boundary="b-x" data-next-state="error" '
    'data-next-error="error"><p>sec error</p></div></section></html>'
)
_ROOT_CAUGHT = (
    '<html><div id="b-x" data-next-boundary="b-x" data-next-state="error" '
    'data-next-error="error"><p>root error</p></div></html>'
)


def _masked(html: str) -> str:
    """Return `html` with the per-render boundary id written as `b-x`."""
    return re.sub(r"b-[0-9a-f]+-[0-9]+", "b-x", html)


def _shown(content: bytes) -> str:
    """Return the page with the per-render boundary id written as `b-x`."""
    return _masked(content.decode())


@pytest.fixture()
def bounded(tmp_path: Path) -> Path:
    """Return a `pages` root, `<html>` over `sec/<section>`, an `error.djx` at both."""
    pages = tmp_path / "pages"
    (pages / "sec").mkdir(parents=True)
    (pages / "layout.djx").write_text("<html>{% template %}</html>")
    (pages / "error.djx").write_text(_ROOT_ERROR)
    (pages / "sec" / "layout.djx").write_text("<section>{% template %}</section>")
    (pages / "sec" / "error.djx").write_text(_SEC_ERROR)
    return pages


def _get(pages: Path, url: str, **client: object) -> HttpResponse:
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
        return Client(**client).get(url)


@pytest.mark.django_db()
class TestPageErrorBoundaries:
    """The nearest `error.djx` contains a failed page body inside the layouts above it."""

    @pytest.mark.parametrize(
        ("source", "body"),
        [
            pytest.param(_EXPLODING, "{{ boom }}", id="template"),
            pytest.param(_FAILING_CONTEXT, "{{ value }}", id="context"),
            pytest.param(_FAILING_RENDER, None, id="render"),
            pytest.param(
                _FAILING_RENDER.replace("def ", "async def "), None, id="async-render"
            ),
            pytest.param(_FAILING_ASYNC_CONTEXT, None, id="async-context"),
        ],
    )
    def test_a_failure_renders_the_nearest_error_file(
        self, bounded, source: str, body: str | None
    ) -> None:
        write_page(bounded / "sec" / "leaf", source=source, body=body)

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 500
        assert _shown(response.content) == _SEC_CAUGHT

    def test_a_degraded_page_never_goes_to_a_shared_cache(self, bounded) -> None:
        write_page(
            bounded / "sec" / "leaf",
            source=f"cache = 60\n{_EXPLODING}",
            body="{{ boom }}",
        )

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 500
        assert response["Cache-Control"] == "private, no-store"

    def test_a_context_failure_renders_without_context_processors(
        self, bounded, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(manager_render, "_get_context_processors", list)
        write_page(
            bounded / "sec" / "leaf", source=_FAILING_CONTEXT, body="{{ value }}"
        )

        response = _get(bounded, "/sec/leaf/")

        assert _shown(response.content) == _SEC_CAUGHT

    def test_a_layout_callable_of_the_boundary_directory_is_outside_it(
        self, bounded
    ) -> None:
        write_page(bounded / "sec", source=_FAILING_INHERITED)
        write_page(bounded / "sec" / "leaf", body="{{ shared }}")

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 500
        assert _shown(response.content) == _ROOT_CAUGHT

    def test_the_own_directory_layout_is_outside_its_boundary(self, bounded) -> None:
        (bounded / "sec" / "layout.djx").write_text(
            "<section>{{ boom }}{% template %}</section>"
        )
        write_page(bounded / "sec" / "leaf", source=_EXPLODING, body="<p>ok</p>")

        response = _get(bounded, "/sec/leaf/")

        assert _shown(response.content) == _ROOT_CAUGHT

    def test_a_working_async_page_under_a_boundary_renders(self, bounded) -> None:
        write_page(bounded / "sec" / "leaf", source=_WORKING_ASYNC)

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 200
        assert response.content == b"<html><section><h1>fine</h1></section></html>"

    @pytest.mark.parametrize(
        ("source", "status"),
        [(_RAISES_404, 404), (_RAISES_403, 403), (_RAISES_400, 400)],
    )
    def test_an_intended_exception_keeps_its_django_answer(
        self, bounded, source: str, status: int
    ) -> None:
        write_page(bounded / "sec" / "leaf", source=source)

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == status

    def test_a_failing_error_file_falls_to_the_boundary_above(self, bounded) -> None:
        (bounded / "sec" / "error.djx").write_text(_BROKEN_ERROR)
        write_page(bounded / "sec" / "leaf", source=_EXPLODING, body="{{ boom }}")

        response = _get(bounded, "/sec/leaf/")

        assert _shown(response.content) == _ROOT_CAUGHT

    def test_past_the_last_boundary_django_answers(self, bounded) -> None:
        (bounded / "sec" / "error.djx").write_text(_BROKEN_ERROR)
        (bounded / "error.djx").write_text(_BROKEN_ERROR)
        write_page(bounded / "sec" / "leaf", source=_EXPLODING, body="{{ boom }}")

        response = _get(bounded, "/sec/leaf/", raise_request_exception=False)

        assert response.status_code == 500
        assert b"section" not in response.content

    def test_a_component_boundary_inside_catches_first(self, bounded) -> None:
        write_component(
            bounded,
            "widget",
            module=_FAILING_WIDGET,
            body="<b>{{ value }}</b>",
            loading=None,
            error="<i>widget {{ error.kind }}</i>",
        )
        write_page(
            bounded / "sec" / "leaf",
            source="x = 1\n",
            body='[{% component "widget" %}]',
        )

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 200
        assert "<i>widget error</i>" in response.content.decode()
        assert "sec error" not in response.content.decode()

    def test_the_render_signals_report_the_error_path(self, bounded) -> None:
        write_page(
            bounded / "sec" / "leaf", source=_FAILING_CONTEXT, body="{{ value }}"
        )

        with capture_signals(page_rendered, boundary_failed) as recorder:
            _get(bounded, "/sec/leaf/")

        rendered = recorder.first_for(page_rendered)
        failed = recorder.first_for(boundary_failed)
        assert rendered.kwargs["file_path"].parts[-2:] == ("leaf", "page.py")
        assert rendered.kwargs["duration_ms"] >= 0
        assert failed.kwargs["addressing"] == "page"
        assert failed.kwargs["name"].endswith("/sec/error.djx")

    def test_a_page_without_a_boundary_file_lets_the_failure_out(
        self, tmp_path
    ) -> None:
        pages = tmp_path / "pages"
        write_page(pages / "leaf", source=_FAILING_CONTEXT, body="{{ value }}")

        with pytest.raises(ValueError, match="context"):
            _get(pages, "/leaf/")

    def test_a_render_without_a_request_renders_the_error_file(self, bounded) -> None:
        page_file = write_page(
            bounded / "sec" / "leaf", source=_FAILING_CONTEXT, body="{{ value }}"
        )

        html = page.render(page_file)

        assert _masked(html) == _SEC_CAUGHT

    def test_a_zone_request_keeps_its_failure(self, bounded) -> None:
        page_file = write_page(bounded / "sec" / "leaf", source=_FAILING_RENDER)

        with pytest.raises(ValueError, match="render"):
            unified_view(page, page_file)(build_zone_request("z"))

    def test_a_boundary_file_added_in_dev_reaches_the_next_request(
        self, tmp_path, watched_template_edits
    ) -> None:
        pages = tmp_path / "pages"
        page_file = write_page(pages, source=_EXPLODING, body="{{ boom }}")
        view = unified_view(page, page_file)
        with pytest.raises(ValueError, match="body"):
            view(build_page_request())

        (pages / "error.djx").write_text(_ROOT_ERROR)
        touch_later(pages)
        response = view(build_page_request())

        assert response.status_code == 500
        assert "root error" in response.content.decode()


_NAV_ROOT = """
from next.pages import context

template = "<p>root</p>"


@context("nav", inherit_context=True)
def nav():
    return "NAV"
"""

_SECRET_THEN_FAILING = """
from next.pages import context


@context("secret")
def secret():
    return "S3CRET"


@context("value")
def value():
    raise ValueError("context")
"""

_SECRET_THEN_EXPLODING = f"""
{_EXPLODING}

@context("secret")
def secret():
    return "S3CRET"
"""

_SIDE_THEN_FAILING = """
from next.pages import context

template = "<p>sec</p>"


@context("side", inherit_context=True)
def side():
    return "SIDE"


@context("shared", inherit_context=True)
def shared():
    raise ValueError("inherited")
"""

_FAILING_RENDER_ASYNC_CONTEXT = """
from next.pages import context


@context("own")
async def own():
    return "OWN"


def render():
    raise ValueError("render")
"""

_EXTENDS_BASE = """
from django.template import Template
from next.pages import context


@context("base")
def base():
    return Template("<b>{% block c %}{% endblock %}</b>")
"""

_SHELL_ERROR = "<p>err [{{ nav }}] [{{ secret }}] [{{ side }}]</p>"


def _caught_body(content: bytes) -> str:
    """Return the error UI inside the page boundary wrapper."""
    return _shown(content).split('data-next-error="error">', 1)[1].split("</div>")[0]


@pytest.fixture()
def navigated(tmp_path: Path) -> Path:
    """Return a `pages` root whose page inherits `nav`, the layout printing it."""
    pages = tmp_path / "pages"
    write_page(pages, source=_NAV_ROOT)
    (pages / "layout.djx").write_text("<html>[{{ nav }}]{% template %}</html>")
    (pages / "leaf").mkdir()
    (pages / "leaf" / "error.djx").write_text(_SHELL_ERROR)
    return pages


@pytest.mark.django_db()
class TestPageBoundaryShell:
    """The layouts above a boundary and its `error.djx` share one shell."""

    @pytest.mark.parametrize(
        ("source", "body", "leaks"),
        [
            pytest.param(_SECRET_THEN_FAILING, "{{ value }}", (), id="context"),
            pytest.param(
                _SECRET_THEN_EXPLODING,
                "{{ secret }}{{ boom }}",
                (b"S3CRET",),
                id="template",
            ),
            pytest.param(_FAILING_RENDER, None, (), id="render"),
            pytest.param(
                _FAILING_RENDER_ASYNC_CONTEXT, None, (b"OWN",), id="async-render"
            ),
        ],
    )
    def test_a_failure_keeps_the_inherited_context_above_the_cut(
        self, navigated, source: str, body: str | None, leaks: tuple[bytes, ...]
    ) -> None:
        write_page(navigated / "leaf", source=source, body=body)

        response = _get(navigated, "/leaf/")

        assert response.status_code == 500
        assert _shown(response.content).startswith("<html>[NAV]<div")
        assert _caught_body(response.content) == "<p>err [NAV] [] []</p>"
        for leak in leaks:
            assert leak not in response.content

    def test_a_group_below_the_cut_never_reaches_the_error_file(
        self, navigated
    ) -> None:
        (navigated / "error.djx").write_text(_SHELL_ERROR)
        write_page(navigated / "sec", source=_SIDE_THEN_FAILING)
        write_page(navigated / "sec" / "leaf", body="{{ shared }}")

        response = _get(navigated, "/sec/leaf/")

        assert _caught_body(response.content) == "<p>err [NAV] [] []</p>"

    def test_a_variable_the_body_sets_reaches_the_layout(self, tmp_path) -> None:
        pages = tmp_path / "pages"
        write_page(pages / "leaf", source="x = 1\n", body='{% firstof "SET" as t %}b')
        (pages / "layout.djx").write_text("<html>{% template %}|{{ t }}</html>")
        (pages / "leaf" / "error.djx").write_text("e")

        response = _get(pages, "/leaf/")

        assert response.content == b"<html>b|SET</html>"

    def test_a_body_opening_with_extends_renders_under_a_boundary(
        self, tmp_path
    ) -> None:
        pages = tmp_path / "pages"
        (pages / "a").mkdir(parents=True)
        (pages / "error.djx").write_text(_ROOT_ERROR)
        write_page(
            pages / "a",
            source=_EXTENDS_BASE,
            body="{% extends base %}{% block c %}hi{% endblock %}",
        )

        response = _get(pages, "/a/")

        assert response.status_code == 200
        assert response.content == b"<b>hi</b>"

    def test_a_failing_body_opening_with_extends_is_contained(self, tmp_path) -> None:
        pages = tmp_path / "pages"
        (pages / "a").mkdir(parents=True)
        (pages / "error.djx").write_text(_ROOT_ERROR)
        write_page(
            pages / "a",
            source=f"{_EXTENDS_BASE}\n{_EXPLODING}",
            body="{% extends base %}{% block c %}{{ boom }}{% endblock %}",
        )

        response = _get(pages, "/a/")

        assert response.status_code == 500
        assert _caught_body(response.content) == "<p>root error</p>"

    def test_debug_names_the_error_file_that_caught(self, bounded) -> None:
        write_page(bounded / "sec" / "leaf", source=_EXPLODING, body="{{ boom }}")

        with override_settings(DEBUG=True, BASE_DIR=bounded.parent):
            response = _get(bounded, "/sec/leaf/")

        assert 'data-next-boundary-source="pages/sec/error.djx"' in (
            response.content.decode()
        )


@pytest.mark.django_db()
class TestPageBoundaryProcessors:
    """The context processors belong to the shell and run once, outside every guard."""

    @pytest.mark.parametrize("broken", [False, True], ids=["working", "failing"])
    def test_the_processors_run_once_and_a_failing_one_goes_to_django(
        self, bounded, monkeypatch: pytest.MonkeyPatch, *, broken: bool
    ) -> None:
        calls: list[object] = []

        def processor(request: HttpRequest) -> dict[str, object]:
            calls.append(request)
            if broken:
                msg = "processor"
                raise RuntimeError(msg)
            return {"error": "shadowed"}

        monkeypatch.setattr(
            manager_render, "_get_context_processors", lambda: [processor]
        )
        write_page(bounded / "sec" / "leaf", source=_FAILING_CONTEXT, body="x")
        expectation = (
            pytest.raises(RuntimeError, match="processor") if broken else nullcontext()
        )

        with expectation:
            assert _get(bounded, "/sec/leaf/").status_code == 500

        assert len(calls) == 1


_WRITES_SYNC = """
from django.contrib.auth.models import Group
from next.pages import context


@context("value")
def value():
    Group.objects.create(name="sync")
    raise ValueError("context")
"""

_WRITES_ASYNC_CONTEXT = """
from asgiref.sync import sync_to_async
from django.contrib.auth.models import Group
from next.pages import context


@context("value")
async def value():
    await sync_to_async(Group.objects.create)(name="async context")
    raise ValueError("context")


async def render():
    return "{{ value }}"
"""

_WRITES_ASYNC_RENDER = """
from asgiref.sync import sync_to_async
from django.contrib.auth.models import Group


async def render():
    await sync_to_async(Group.objects.create)(name="async render")
    raise ValueError("render")
"""


@pytest.mark.django_db(transaction=True)
class TestPageBoundaryWrites:
    """A contained page failure undoes its writes on every path, as `ATOMIC` says."""

    @pytest.mark.parametrize(
        "source", [_WRITES_SYNC, _WRITES_ASYNC_CONTEXT, _WRITES_ASYNC_RENDER]
    )
    def test_the_writes_of_a_contained_failure_roll_back(
        self, bounded, source: str
    ) -> None:
        write_page(bounded / "sec" / "leaf", source=source, body="{{ value }}")

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 500
        assert list(Group.objects.values_list("name", flat=True)) == []

    @pytest.mark.parametrize(
        ("source", "name"),
        [
            (_WRITES_SYNC, "sync"),
            (_WRITES_ASYNC_CONTEXT, "async context"),
            (_WRITES_ASYNC_RENDER, "async render"),
        ],
    )
    def test_atomic_false_keeps_the_writes_of_autocommit(
        self, bounded, source: str, name: str
    ) -> None:
        write_page(bounded / "sec" / "leaf", source=source, body="{{ value }}")

        defer = {"ERRORS": {"ATOMIC": False}}
        with routed(bounded, COMPONENT_BACKENDS=COMPONENTS, DEFER=defer):
            response = Client().get("/sec/leaf/")

        assert response.status_code == 500
        assert list(Group.objects.values_list("name", flat=True)) == [name]


@pytest.fixture()
def zoned(tmp_path: Path) -> Path:
    """Return a `pages` root with a layout zone and an `error.djx` below its layout."""
    pages = tmp_path / "pages"
    (pages / "z").mkdir(parents=True)
    (pages / "layout.djx").write_text(
        '<html>{% zone "outer" %}o{% endzone %}{% template %}</html>'
    )
    (pages / "z" / "error.djx").write_text(_SEC_ERROR)
    return pages


class TestZoneBoundaries:
    """A zone GET inside a page boundary answers a contained failure with its UI."""

    def test_a_context_failure_answers_with_the_error_ui(self, zoned) -> None:
        page_file = write_page(
            zoned / "z",
            source=_FAILING_CONTEXT,
            body='{% zone "z" %}{{ value }}{% endzone %}',
        )

        response = unified_view(page, page_file)(build_zone_request("z"))

        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        html = _masked(envelope_of(response).html_for_zone("z"))
        assert html == (
            '<div data-next-zone="z"><div id="b-x" data-next-boundary="b-x" '
            'data-next-state="error" data-next-error="error"><p>sec error</p></div></div>'
        )

    def test_a_zone_outside_the_boundary_keeps_its_failure(self, zoned) -> None:
        page_file = write_page(
            zoned / "z",
            source=_FAILING_CONTEXT,
            body='{% zone "z" %}{{ value }}{% endzone %}',
        )

        with pytest.raises(ValueError, match="context"):
            unified_view(page, page_file)(build_zone_request("outer"))

    def test_a_failing_zone_body_is_contained(self, zoned) -> None:
        page_file = write_page(
            zoned / "z", source=_EXPLODING, body='{% zone "z" %}{{ boom }}{% endzone %}'
        )

        response = unified_view(page, page_file)(build_zone_request("z"))

        assert response.status_code == 200
        assert "<p>sec error</p>" in envelope_of(response).html_for_zone("z")

    def test_a_working_zone_inside_a_boundary_renders_again(self, zoned) -> None:
        page_file = write_page(
            zoned / "z", source="x = 1\n", body='{% zone "z" %}<i>ok</i>{% endzone %}'
        )
        view = unified_view(page, page_file)

        first = view(build_zone_request("z"))
        second = view(build_zone_request("z"))

        for response in (first, second):
            assert envelope_of(response).html_for_zone("z") == (
                '<div data-next-zone="z"><i>ok</i></div>'
            )

    def test_an_outer_boundary_answers_past_an_inner_one(self, bounded) -> None:
        sec_page = write_page(bounded / "sec", source=_FAILING_INHERITED)
        load_python_module_memo(sec_page)
        page_file = write_page(
            bounded / "sec" / "leaf",
            source="x = 1\n",
            body='{% zone "z" %}{{ shared }}{% endzone %}',
        )

        response = unified_view(page, page_file)(build_zone_request("z"))

        assert "<p>root error</p>" in envelope_of(response).html_for_zone("z")

    @pytest.mark.parametrize(
        "source", [_FAILING_RENDER, _FAILING_RENDER.replace("def ", "async def ")]
    )
    def test_a_failing_render_of_a_zone_request_keeps_its_failure(
        self, zoned, source: str
    ) -> None:
        page_file = write_page(zoned / "z", source=source)

        with pytest.raises(ValueError, match="render"):
            unified_view(page, page_file)(build_zone_request("z"))

    def test_a_failing_render_answers_a_layout_zone_inside_the_boundary(
        self, zoned
    ) -> None:
        page_file = write_page(zoned / "z" / "leaf", source=_FAILING_RENDER)
        (zoned / "z" / "leaf" / "layout.djx").write_text(
            '<div>{% zone "inner" %}i{% endzone %}{% template %}</div>'
        )

        response = unified_view(page, page_file)(build_zone_request("inner"))

        assert response.status_code == 200
        assert "<p>sec error</p>" in envelope_of(response).html_for_zone("inner")


@pytest.mark.django_db()
class TestBoundaryChainCache:
    """The boundary chain rides the composed layer, read once per request."""

    def test_a_request_never_looks_the_chain_up_again(
        self, bounded, watched_template_edits
    ) -> None:
        page_file = write_page(bounded / "sec" / "leaf", source=_FAILING_RENDER)
        view = unified_view(page, page_file)
        view(build_page_request())

        looked = mock.Mock(wraps=manager_module.page_boundaries)
        with (
            mock.patch.object(manager_module, "page_boundaries", looked),
            mock.patch.object(manager_render, "page_boundaries", looked),
            mock.patch.object(manager_bodies, "page_boundaries", looked),
        ):
            response = view(build_page_request())

        assert response.status_code == 500
        assert looked.call_count == 0
        assert page._templates.skeleton_chains[page_file].error == (
            bounded / "sec" / "error.djx"
        )

    def test_a_page_with_an_error_file_opens_the_render_state(self, bounded) -> None:
        page_file = write_page(bounded / "sec" / "leaf", body="<p>ok</p>")
        plain = write_page(bounded.parent / "plain", body="<p>ok</p>")

        with mock.patch(
            "next.pages.boundaries.render_boundaries", wraps=render_boundaries
        ) as opened:
            unified_view(page, plain)(build_page_request())
            assert opened.call_count == 0
            unified_view(page, page_file)(build_page_request())

        assert opened.call_count >= 1
