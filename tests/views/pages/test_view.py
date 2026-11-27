from pathlib import Path
from unittest import mock

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client

from next.pages.loaders import load_python_module_memo
from next.partial import lifecycle as partial_lifecycle
from next.partial.headers import VARY_HEADERS, partial_intent
from next.requests import RequestKind
from next.testing import envelope_of
from next.views import page_view, pages as view_pages
from tests.support import (
    build_nested_page,
    build_page_request,
    build_zone_request,
    path_under,
    record_path_calls,
    unified_view,
    write_page,
)
from tests.support.views import ZONE_HTML, ZONED_BODY, write_dynamic_page


class TestPageView:
    """The factory the URL router mounts builds a view for every page with a body."""

    def test_a_page_without_a_body_gets_no_view(self, page_instance, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def other_function(): pass")

        assert page_view(page_instance, page_file) is None

    def test_a_static_page_gets_a_view_tagged_with_its_path(
        self, page_instance, tmp_path
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text('template = "<p>static</p>"')

        view = page_view(page_instance, page_file)

        assert view is not None
        assert view.next_page_path == page_file
        assert view(build_page_request()).content == b"<p>static</p>"

    def test_a_dynamic_page_gets_the_resolving_view(
        self, page_instance, tmp_path
    ) -> None:
        page_file = write_dynamic_page(tmp_path / "dynamic")

        view = page_view(page_instance, page_file)

        assert view is not None
        assert view(build_page_request()).content == b"<p>dynamic</p>"


class TestStaticBranch:
    """A page without ``render()`` serves the composed template of its files."""

    def test_page_without_a_module_takes_the_static_branch(
        self, page_instance, tmp_path
    ) -> None:
        """A template-only page with no ``page.py`` serves from the cache too."""
        page_file = tmp_path / "page.py"
        (tmp_path / "template.djx").write_text("<p>virtual</p>")

        view = view_pages.unified_view(page_instance, page_file, None)
        assert view(build_page_request()).content == b"<p>virtual</p>"
        assert page_file in page_instance._templates.composed


class TestUnifiedViewBodyResolution:
    """The view resolves the body via render > template > template.djx."""

    @pytest.fixture()
    def sub(self, tmp_path: Path) -> Path:
        """Return a directory below an ancestor layout that wraps the body in `<body>`."""
        (tmp_path / "layout.djx").write_text("<html><body>{% template %}</body></html>")
        return tmp_path / "sub"

    @pytest.mark.parametrize(
        ("source", "body"),
        [
            pytest.param(
                'template = "<h1>attr body</h1>"', "<h1>attr body</h1>", id="attr"
            ),
            pytest.param(
                "def render(request, **kwargs):\n    return '<p>rendered</p>'\n",
                "<p>rendered</p>",
                id="render-str",
            ),
            pytest.param("", "", id="empty"),
        ],
    )
    def test_a_body_composes_through_the_ancestor_layout(
        self, page_instance, sub, source: str, body: str
    ) -> None:
        page_file = write_page(sub, source=source)

        response = unified_view(page_instance, page_file)(build_page_request())

        assert response.content.decode() == f"<html><body>{body}</body></html>"

    @pytest.mark.parametrize(
        ("construct", "status", "content", "headers"),
        [
            pytest.param(
                "HttpResponse('raw', status=201)", 201, b"raw", {}, id="response"
            ),
            pytest.param(
                "HttpResponseRedirect('/target/')",
                302,
                b"",
                {"Location": "/target/"},
                id="redirect",
            ),
            pytest.param(
                "JsonResponse({'ok': True})",
                200,
                b'{"ok": true}',
                {"Content-Type": "application/json"},
                id="json",
            ),
        ],
    )
    def test_a_response_returned_by_render_bypasses_the_layout(
        self,
        page_instance,
        sub,
        construct: str,
        status: int,
        content: bytes,
        headers: dict[str, str],
    ) -> None:
        source = (
            "from django.http import HttpResponse, HttpResponseRedirect, JsonResponse\n"
            "def render(request, **kwargs):\n"
            f"    return {construct}\n"
        )
        page_file = write_page(sub, source=source)

        response = unified_view(page_instance, page_file)(build_page_request())

        assert response.status_code == status
        assert response.content == content
        for name, value in headers.items():
            assert response[name] == value

    @pytest.mark.parametrize(
        "return_value",
        ["None", "{'x': 1}", "[1, 2]", "42"],
        ids=["None", "dict", "list", "int"],
    )
    def test_render_returning_non_str_non_response_raises(
        self, page_instance, tmp_path, return_value
    ) -> None:
        """`render()` returning anything other than str/HttpResponse raises TypeError."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            f"def render(request, **kwargs):\n    return {return_value}\n"
        )

        view = unified_view(page_instance, page_file)
        with pytest.raises(TypeError, match="must return str or HttpResponse"):
            view(build_page_request())

    def test_render_raising_propagates(self, page_instance, tmp_path) -> None:
        """`render()` raising an exception propagates to the caller."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "def render(request, **kwargs):\n    raise RuntimeError('boom')\n"
        )

        view = unified_view(page_instance, page_file)
        with pytest.raises(RuntimeError, match="boom"):
            view(build_page_request())

    def test_priority_render_wins_over_template_attr(
        self, page_instance, tmp_path
    ) -> None:
        """When both render() and template attr exist, render() wins."""
        page_file = tmp_path / "page.py"
        page_file.write_text(
            'template = "<p>from-attr</p>"\n'
            "def render(request, **kwargs):\n"
            "    return '<p>from-render</p>'\n"
        )

        view = unified_view(page_instance, page_file)
        response = view(build_page_request())
        assert b"from-render" in response.content
        assert b"from-attr" not in response.content

    def test_priority_template_attr_wins_over_template_djx(
        self, page_instance, tmp_path
    ) -> None:
        """When both template attr and template.djx exist, attr wins."""
        (tmp_path / "template.djx").write_text("<p>from-djx</p>")
        page_file = tmp_path / "page.py"
        page_file.write_text('template = "<p>from-attr</p>"')

        view = unified_view(page_instance, page_file)
        response = view(build_page_request())
        assert b"from-attr" in response.content
        assert b"from-djx" not in response.content


class TestZoneTickOnTheStaticBranch:
    """A zone GET of a page without ``render()`` resolves no body of its own."""

    def test_the_first_tick_reads_the_body_once(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """Only the composition opens ``template.djx``, nothing reads it twice."""
        page_file = build_nested_page(tmp_path, body=ZONED_BODY)
        view = unified_view(page_instance, page_file)
        reads = record_path_calls(monkeypatch, "read_text", path_under(tmp_path))

        response = view(build_zone_request("z"))

        assert response.status_code == 200
        assert reads.count(page_file.parent / "template.djx") == 1

    def test_a_warm_tick_opens_no_file(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """The second tick serves the cached composition without a read."""
        page_file = build_nested_page(tmp_path, body=ZONED_BODY)
        view = unified_view(page_instance, page_file)
        view(build_zone_request("z"))
        reads = record_path_calls(monkeypatch, "read_text", path_under(tmp_path))

        response = view(build_zone_request("z"))

        assert reads == []
        assert envelope_of(response).html_for_zone("z") == ZONE_HTML

    def test_a_tick_never_enters_body_resolution(self, page_instance, tmp_path) -> None:
        """Nothing about a static body depends on the request, so it is not resolved."""
        page_file = build_nested_page(tmp_path, body=ZONED_BODY)
        view = unified_view(page_instance, page_file)

        with mock.patch.object(page_instance, "resolve_page_body") as resolve:
            response = view(build_zone_request("z"))

        assert resolve.call_count == 0
        assert response.status_code == 200
        assert envelope_of(response).html_for_zone("z") == ZONE_HTML
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
        module = load_python_module_memo(page_file)

        response = unified_view(page_instance, page_file)(build_zone_request("z"))

        assert response.status_code == 400
        assert module.calls == 1

    def test_a_zone_in_a_dynamic_body_is_a_bad_request(
        self, page_instance, tmp_path
    ) -> None:
        """A body built per request has no compiled source to render a zone from."""
        page_file = write_dynamic_page(
            tmp_path, returns="'{% zone \"z\" %}x{% endzone %}'"
        )

        response = unified_view(page_instance, page_file)(build_zone_request("z"))

        assert response.status_code == 400
        assert response.content == b"zone in dynamic body"

    def test_a_render_redirect_short_circuits_the_tick(
        self, page_instance, tmp_path
    ) -> None:
        """A redirect is returned for the tick before the shaper is consulted."""
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


class TestViewFollowsTheRouteDecision:
    """Both branches answer the route the decision names, not the raw headers."""

    def test_a_static_zone_request_routed_as_a_page_renders_in_full(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body=ZONED_BODY)
        view = unified_view(page_instance, page_file)

        with mock.patch.object(
            view_pages, "route_request", return_value=RequestKind.PAGE
        ):
            response = view(build_zone_request("z"))

        assert response.content.startswith(b"<html><main>a ")

    def test_a_dynamic_zone_request_routed_as_a_page_renders_in_full(
        self, page_instance, tmp_path
    ) -> None:
        page_file = write_dynamic_page(tmp_path / "dynamic")
        view = unified_view(page_instance, page_file)

        with mock.patch.object(
            view_pages, "route_request", return_value=RequestKind.PAGE
        ):
            response = view(build_zone_request("z"))

        assert response.content == b"<p>dynamic</p>"


class TestPlainPageRequest:
    """A request naming no zone reads the intent and never builds a zone envelope."""

    @pytest.fixture()
    def intent_reads(self):
        """Spy on the intent reads and fail any entry into the zone envelope."""
        refused = AssertionError("a request naming no zone built a zone envelope")
        with (
            mock.patch.object(
                partial_lifecycle, "partial_intent", wraps=partial_intent
            ) as reads,
            mock.patch.object(partial_lifecycle, "zone_response", side_effect=refused),
        ):
            yield reads

    def test_page_renders_in_full(self, intent_reads) -> None:
        response = Client().get("/zoned/")
        assert response.status_code == 200
        assert b"<h1>zoned page</h1>" in response.content


class TestFullPageResponseDeclaresItsNegotiation:
    """A full page renders under the request headers the partial layer reads."""

    def test_the_static_branch_varies_on_every_partial_header(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path)

        response = unified_view(page_instance, page_file)(build_page_request())

        varied = {value.strip() for value in response.headers["Vary"].split(",")}
        assert set(VARY_HEADERS) <= varied

    def test_the_render_branch_varies_on_every_partial_header(
        self, page_instance, tmp_path
    ) -> None:
        page_file = write_dynamic_page(tmp_path / "dynamic")

        response = unified_view(page_instance, page_file)(build_page_request())

        varied = {value.strip() for value in response.headers["Vary"].split(",")}
        assert set(VARY_HEADERS) <= varied

    def test_a_page_short_circuiting_with_a_redirect_is_left_alone(self) -> None:
        response = Client().get("/redirecting/")
        varied = {name.strip().lower() for name in response.get("Vary", "").split(",")}
        assert response.status_code == 302
        assert varied.isdisjoint(name.lower() for name in VARY_HEADERS)


_RENDER_ARGUMENT_NAMES = (
    "dep_cache",
    "file_path",
    "namespace",
    "requested_zones",
    "url_kwargs",
)


class TestRenderArgumentNamesAsUrlKwargs:
    """A URL kwarg named after a render pipeline argument reaches the page intact."""

    @pytest.mark.parametrize("name", _RENDER_ARGUMENT_NAMES)
    def test_a_static_page_renders_the_value(
        self, page_instance, tmp_path, name
    ) -> None:
        page_file = build_nested_page(tmp_path, body=f"<h1>{{{{ {name} }}}}</h1>")
        view = unified_view(page_instance, page_file)

        response = view(build_page_request(), **{name: "v"})

        assert response.content.decode() == "<html><main><h1>v</h1></main></html>"

    @pytest.mark.parametrize("name", _RENDER_ARGUMENT_NAMES)
    def test_a_dynamic_page_receives_the_value(
        self, page_instance, tmp_path, name
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            f"def render({name}: str) -> str:\n    return '<p>' + {name}\n"
        )
        view = unified_view(page_instance, page_file)

        response = view(build_page_request(), **{name: "v"})

        assert response.content.decode() == "<p>v"

    @pytest.mark.parametrize("name", _RENDER_ARGUMENT_NAMES)
    def test_a_zone_tick_renders_the_value(self, page_instance, tmp_path, name) -> None:
        body = f'{{% zone "z" %}}<p>{{{{ {name} }}}}</p>{{% endzone %}}'
        page_file = build_nested_page(tmp_path, body=body)
        view = unified_view(page_instance, page_file)

        response = view(build_zone_request("z"), **{name: "v"})

        assert envelope_of(response).html_for_zone("z") == (
            '<div data-next-zone="z"><p>v</p></div>'
        )


@pytest.mark.usefixtures("router_on")
class TestRouterDirectivesOnABuiltResponse:
    """A response `render()` returns carries the router directives a render would."""

    def test_a_clear_reaches_the_response_render_returned(
        self, page_instance, tmp_path
    ) -> None:
        page_file = tmp_path / "cleared" / "page.py"
        page_file.parent.mkdir()
        page_file.write_text(
            "from django.http import HttpRequest, HttpResponse\n"
            "from next.router import router_clear\n\n"
            "def render(request: HttpRequest):\n"
            "    router_clear(request, 'login')\n"
            "    return HttpResponse('built')\n"
        )
        response = unified_view(page_instance, page_file)(build_page_request())
        assert response.content == b"built"
        assert response["X-Next-Router"] == "clear"
