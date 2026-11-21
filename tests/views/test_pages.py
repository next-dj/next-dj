import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.handlers.base import BaseHandler
from django.db import connection, connections, transaction
from django.db.utils import ConnectionHandler
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, override_settings

import next.pages.manager as manager_module
from next.boundaries import boundary_failed, render_boundaries
from next.pages import page
from next.pages.loaders import load_python_module_memo
from next.pages.signals import page_rendered
from next.pages.transactions import atomic_declarations
from next.partial.headers import VARY_HEADERS, partial_intent
from next.router import CurrentLinks
from next.suspense.tokens.transport import HEADER
from next.testing import capture_signals, envelope_of, override_next_settings
from next.transactions import ViewKind, request_atomic
from next.views import Route, page_view, pages as view_pages
from tests.pages.checks.metadata.trees import COMPONENTS, component_template
from tests.support import (
    bound_dependency,
    build_nested_page,
    build_page_request,
    build_zone_request,
    path_under,
    record_path_calls,
    routed,
    touch_later,
    unified_view,
    write_page,
)


User = get_user_model()

_ZONED_BODY = 'a {% zone "z" %}<p>zoned</p>{% endzone %} b'
_ZONE_HTML = '<div data-next-zone="z"><p>zoned</p></div>'


def _build_dynamic_page(directory: Path, *, returns: str = "'<p>dynamic</p>'") -> Path:
    """Write a ``page.py`` whose ``render()`` returns `returns` on every request."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(f"def render(request):\n    return {returns}\n")
    return page_file


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
        page_file = _build_dynamic_page(tmp_path / "dynamic")

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

    @pytest.fixture(autouse=True)
    def _isolate(self):
        page._templates.composed.clear()
        page._templates.composed_sources.clear()
        yield
        page._templates.composed.clear()
        page._templates.composed_sources.clear()

    def test_template_attribute_with_ancestor_layout_composes(
        self, page_instance, tmp_path
    ) -> None:
        """`template = "..."` flows through an ancestor `layout.djx`."""
        (tmp_path / "layout.djx").write_text("<html><body>{% template %}</body></html>")
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text('template = "<h1>attr body</h1>"')

        view = unified_view(page_instance, page_file)
        response = view(build_page_request())
        body = response.content.decode()
        assert "<html><body>" in body
        assert "<h1>attr body</h1>" in body
        assert "</body></html>" in body

    def test_render_returning_str_with_ancestor_layout_composes(
        self, page_instance, tmp_path
    ) -> None:
        """`render()` returning a string flows through the ancestor layout."""
        (tmp_path / "layout.djx").write_text("<html><body>{% template %}</body></html>")
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "def render(request, **kwargs):\n    return '<p>rendered</p>'\n"
        )

        view = unified_view(page_instance, page_file)
        response = view(build_page_request())
        body = response.content.decode()
        assert "<html><body>" in body
        assert "<p>rendered</p>" in body

    def test_render_returning_httpresponse_bypasses_layout(
        self, page_instance, tmp_path
    ) -> None:
        """`render()` returning HttpResponse is returned verbatim, no layout."""
        (tmp_path / "layout.djx").write_text("<html><body>{% template %}</body></html>")
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "from django.http import HttpResponse\n"
            "def render(request, **kwargs):\n"
            "    return HttpResponse('raw', status=201)\n"
        )

        view = unified_view(page_instance, page_file)
        response = view(build_page_request())
        assert response.status_code == 201
        assert response.content == b"raw"
        assert "<html>" not in response.content.decode()

    def test_render_returning_redirect_bypasses_layout(
        self, page_instance, tmp_path
    ) -> None:
        """`HttpResponseRedirect` (an HttpResponse subclass) is returned verbatim."""
        (tmp_path / "layout.djx").write_text("<html>{% template %}</html>")
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "from django.http import HttpResponseRedirect\n"
            "def render(request, **kwargs):\n"
            "    return HttpResponseRedirect('/target/')\n"
        )

        view = unified_view(page_instance, page_file)
        response = view(build_page_request())
        assert response.status_code == 302
        assert response["Location"] == "/target/"

    def test_render_returning_jsonresponse_bypasses_layout(
        self, page_instance, tmp_path
    ) -> None:
        """`JsonResponse` (an HttpResponse subclass) is returned verbatim."""
        (tmp_path / "layout.djx").write_text("<html>{% template %}</html>")
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text(
            "from django.http import JsonResponse\n"
            "def render(request, **kwargs):\n"
            "    return JsonResponse({'ok': True})\n"
        )

        view = unified_view(page_instance, page_file)
        response = view(build_page_request())
        assert response["Content-Type"].startswith("application/json")
        assert response.content == b'{"ok": true}'

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

    def test_empty_body_with_layout_renders_layout_shell(
        self, page_instance, tmp_path
    ) -> None:
        """A page with no body source still renders the ancestor layout's shell."""
        (tmp_path / "layout.djx").write_text("<html><body>{% template %}</body></html>")
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text("")

        view = unified_view(page_instance, page_file)
        response = view(build_page_request())
        body = response.content.decode()
        assert "<html><body>" in body
        assert "</body></html>" in body


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

        with mock.patch.object(page_instance, "resolve_page_body") as resolve:
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
        module = load_python_module_memo(page_file)

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
        page_file = build_nested_page(tmp_path, body=_ZONED_BODY)
        view = unified_view(page_instance, page_file)

        with mock.patch.object(view_pages, "route_request", return_value=Route.PAGE):
            response = view(build_zone_request("z"))

        assert response.content.startswith(b"<html><main>a ")

    def test_a_dynamic_zone_request_routed_as_a_page_renders_in_full(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _build_dynamic_page(tmp_path / "dynamic")
        view = unified_view(page_instance, page_file)

        with mock.patch.object(view_pages, "route_request", return_value=Route.PAGE):
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
                view_pages, "partial_intent", wraps=partial_intent
            ) as reads,
            mock.patch.object(view_pages, "zone_response", side_effect=refused),
        ):
            yield reads

    def test_page_renders_in_full(self, intent_reads) -> None:
        response = Client().get("/zoned/")
        assert response.status_code == 200
        assert b"<h1>zoned page</h1>" in response.content

    def test_the_intent_is_read(self, intent_reads) -> None:
        Client().get("/zoned/")
        assert intent_reads.call_count > 0


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
        page_file = _build_dynamic_page(tmp_path / "dynamic")

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


_KINDS_SEEN: list[ViewKind] = []


def record_kind(request: HttpRequest, kind: ViewKind) -> tuple[str, ...]:
    _KINDS_SEEN.append(kind)
    return ()


def _writing_page(
    directory: Path, *, declaration: str = "", ending: str = "return '<p>written</p>'"
) -> Path:
    """Write a `page.py` whose `render()` writes a row and records its transaction."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(
        "from django.contrib.auth.models import Group\n"
        "from django.db import connection, transaction\n"
        "from django.http import HttpResponse\n"
        "from next.pages import page\n"
        f"{declaration}\n"
        "EVENTS = []\n\n\n"
        "def render(request):\n"
        "    transaction.on_commit(lambda: EVENTS.append('commit'))\n"
        "    EVENTS.append(f'atomic={connection.in_atomic_block}')\n"
        "    Group.objects.create(name='from-page')\n"
        f"    {ending}\n"
    )
    return page_file


def _request(method: str) -> HttpRequest:
    request = build_page_request()
    request.method = method
    return request


def _events(page_file: Path) -> list[str]:
    return load_python_module_memo(page_file).EVENTS


def _recorder(events: list[str]) -> Callable[..., object]:
    @contextmanager
    def atomic(using: str | None = None) -> Iterator[None]:
        events.append(f"enter {using}")
        try:
            yield
        finally:
            events.append(f"exit {using}")

    return atomic


@pytest.fixture()
def _fresh_atomic_declarations() -> Iterator[None]:
    atomic_declarations.reset()
    yield
    atomic_declarations.reset()


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("_fresh_atomic_declarations")
class TestPageTransaction:
    """A page view opens the transaction the method rule or `page.atomic` asks for."""

    def test_a_get_runs_outside_a_transaction(self, page_instance, tmp_path) -> None:
        page_file = _writing_page(tmp_path)
        unified_view(page_instance, page_file)(_request("GET"))
        assert _events(page_file) == ["commit", "atomic=False"]
        assert Group.objects.filter(name="from-page").exists()

    @pytest.mark.parametrize("atomic_requests", [True, False])
    def test_a_post_runs_in_one_transaction_with_the_hook_after_commit(
        self, page_instance, tmp_path, *, atomic_requests: bool
    ) -> None:
        page_file = _writing_page(tmp_path)
        view = unified_view(page_instance, page_file)
        with mock.patch.dict(
            connections.settings["default"], {"ATOMIC_REQUESTS": atomic_requests}
        ):
            BaseHandler().make_view_atomic(view)(_request("POST"))
        assert _events(page_file) == ["atomic=True", "commit"]
        assert Group.objects.filter(name="from-page").exists()

    def test_an_exception_rolls_back(self, page_instance, tmp_path) -> None:
        page_file = _writing_page(tmp_path, ending="raise ValueError('page failed')")
        view = unified_view(page_instance, page_file)
        with pytest.raises(ValueError, match="page failed"):
            view(_request("POST"))
        assert _events(page_file) == ["atomic=True"]
        assert not Group.objects.filter(name="from-page").exists()

    @pytest.mark.parametrize("status", [404, 500])
    def test_an_error_response_commits(
        self, page_instance, tmp_path, status: int
    ) -> None:
        page_file = _writing_page(
            tmp_path, ending=f"return HttpResponse(status={status})"
        )
        response = unified_view(page_instance, page_file)(_request("POST"))
        assert response.status_code == status
        assert _events(page_file) == ["atomic=True", "commit"]
        assert Group.objects.filter(name="from-page").exists()

    def test_page_atomic_on_wraps_a_get(self, page_instance, tmp_path) -> None:
        page_file = _writing_page(tmp_path, declaration="page.atomic(True)")
        unified_view(page_instance, page_file)(_request("GET"))
        assert _events(page_file) == ["atomic=True", "commit"]

    def test_page_atomic_off_leaves_a_post_bare(self, page_instance, tmp_path) -> None:
        page_file = _writing_page(tmp_path, declaration="page.atomic(False)")
        unified_view(page_instance, page_file)(_request("POST"))
        assert _events(page_file) == ["commit", "atomic=False"]


class _Refused:
    """A signed-in user refused every permission, noting the transaction asked in."""

    is_authenticated = True

    def __init__(self) -> None:
        self.asked: list[bool] = []

    def has_perm(self, perm: str, obj: object = None) -> bool:
        self.asked.append(connection.in_atomic_block)
        return False


_GUARDED = "page.atomic(True)\npage.permission_required('auth.view_group')"
_PARTIAL = {"X-Next-Request": "1"}
_ROUTE_HEADERS = {
    "page": {},
    "zone": {**_PARTIAL, "X-Next-Zone": "z"},
    "navigation": {**_PARTIAL, "X-Next-Navigate": "1", "Sec-Fetch-Site": "same-origin"},
    "boundary": {**_PARTIAL, HEADER: "token"},
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("_fresh_atomic_declarations")
class TestRefusalBeforeTransaction:
    """A page refuses a request before it opens the transaction `page.atomic` asks."""

    @pytest.fixture(autouse=True)
    def _router(self) -> Iterator[None]:
        with override_next_settings(ROUTER={"ENABLED": True}):
            yield

    def _opened(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
        opened: list[tuple[str, ...]] = []

        def recording(aliases: tuple[str, ...]) -> AbstractContextManager[None]:
            if aliases:
                opened.append(aliases)
            return request_atomic(aliases)

        monkeypatch.setattr(view_pages, "request_atomic", recording)
        return opened

    def _request(self, route: str, user: object) -> HttpRequest:
        request = RequestFactory().get("/", headers=_ROUTE_HEADERS[route])
        request.user = user
        return request

    @pytest.mark.parametrize("route", list(_ROUTE_HEADERS))
    @pytest.mark.parametrize("dynamic", [True, False], ids=["render", "static"])
    def test_a_refused_request_opens_no_transaction(
        self,
        page_instance,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        django_assert_num_queries,
        route: str,
        *,
        dynamic: bool,
    ) -> None:
        page_file = (
            _writing_page(tmp_path, declaration=_GUARDED)
            if dynamic
            else write_page(
                tmp_path,
                "",
                f"from next.pages import page\n{_GUARDED}\n",
                body=_ZONED_BODY,
            )
        )
        view = unified_view(page_instance, page_file)
        user = _Refused()
        opened = self._opened(monkeypatch)
        with django_assert_num_queries(0):
            if route == "boundary":
                assert view(self._request(route, user)).status_code == 403
            else:
                with pytest.raises(PermissionDenied):
                    view(self._request(route, user))
        assert user.asked == [False]
        assert opened == []
        assert not Group.objects.filter(name="from-page").exists()

    def test_an_anonymous_request_is_sent_to_log_in_outside_it(
        self, page_instance, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page_file = _writing_page(tmp_path, declaration=_GUARDED)
        opened = self._opened(monkeypatch)
        response = unified_view(page_instance, page_file)(
            self._request("page", AnonymousUser())
        )
        assert response.status_code == 302
        assert opened == []

    def test_an_admitted_request_runs_in_the_transaction(
        self, page_instance, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened = self._opened(monkeypatch)
        page_file = _writing_page(tmp_path, declaration=_GUARDED)
        user = User.objects.create_user("ada")
        user.user_permissions.add(Permission.objects.get(codename="view_group"))
        response = unified_view(page_instance, page_file)(
            self._request("page", User.objects.get(pk=user.pk))
        )
        assert response.status_code == 200
        assert _events(page_file) == ["atomic=True", "commit"]
        assert Group.objects.filter(name="from-page").exists()
        assert opened == [("default",)]


class TestPageTransactionWiring:
    """The view stays outside `ATOMIC_REQUESTS` and names its kind to the rule."""

    @pytest.fixture(autouse=True)
    def _isolate(self) -> Iterator[None]:
        page._templates.composed.clear()
        page._templates.composed_sources.clear()
        _KINDS_SEEN.clear()
        yield
        page._templates.composed.clear()
        page._templates.composed_sources.clear()

    def test_a_get_opens_nothing_and_touches_no_connection(
        self, page_instance, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)
        opened: list[str] = []
        touched: list[str] = []

        def touch(handler: ConnectionHandler, alias: str) -> None:
            touched.append(alias)

        monkeypatch.setattr(transaction, "atomic", _recorder(opened))
        monkeypatch.setattr(ConnectionHandler, "__getitem__", touch)
        response = view(_request("GET"))
        assert response.status_code == 200
        assert opened == []
        assert touched == []

    @pytest.mark.parametrize("atomic_requests", [True, False])
    def test_atomic_requests_adds_no_second_transaction(
        self,
        page_instance,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        atomic_requests: bool,
    ) -> None:
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)
        opened: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(opened))
        with mock.patch.dict(
            connections.settings["default"], {"ATOMIC_REQUESTS": atomic_requests}
        ):
            wrapped = BaseHandler().make_view_atomic(view)
        wrapped(_request("POST"))
        assert wrapped is view
        assert opened == ["enter default", "exit default"]

    @pytest.mark.parametrize("dynamic", [True, False])
    def test_atomic_requests_on_two_aliases_leaves_the_view_unwrapped(
        self, page_instance, tmp_path, monkeypatch: pytest.MonkeyPatch, *, dynamic: bool
    ) -> None:
        page_file = (
            _build_dynamic_page(tmp_path / "dynamic")
            if dynamic
            else build_nested_page(tmp_path)
        )
        view = unified_view(page_instance, page_file)
        declared = {"ATOMIC_REQUESTS": True}
        monkeypatch.setattr(
            connections, "settings", {"default": declared, "replica": declared}
        )
        assert BaseHandler().make_view_atomic(view) is view

    def test_the_rule_hears_the_page_and_the_zone_kind(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body=_ZONED_BODY)
        view = unified_view(page_instance, page_file)
        rule = {"TRANSACTIONS": {"RULE": "tests.views.test_pages.record_kind"}}
        with override_settings(NEXT_FRAMEWORK=rule):
            view(build_page_request())
            view(build_zone_request("z"))
        assert _KINDS_SEEN == [ViewKind.PAGE, ViewKind.ZONE]

    def test_the_rule_hears_the_page_kind_on_a_dynamic_page(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _build_dynamic_page(tmp_path / "dynamic")
        view = unified_view(page_instance, page_file)
        rule = {"TRANSACTIONS": {"RULE": "tests.views.test_pages.record_kind"}}
        with override_settings(NEXT_FRAMEWORK=rule):
            view(build_page_request())
        assert _KINDS_SEEN == [ViewKind.PAGE]


_ASYNC_STATIC = (
    "import asyncio\n"
    "from next.pages import context\n\n"
    "template = '<p>{{ first }} {{ second }}</p>"
    '{% zone "z" %}<i>{{ zoned }}</i>{% endzone %}\'\n\n'
    "@context('first')\n"
    "async def first():\n"
    "    await asyncio.sleep(0)\n"
    "    return 'A'\n\n"
    "@context('second')\n"
    "def second(first):\n"
    "    return first + 'B'\n\n"
    "@context('zoned', zone='z')\n"
    "async def zoned(first):\n"
    "    return 'Z' + first\n"
)

_SYNC_STATIC = (
    "from next.pages import context\n\n"
    "template = '<p>{{ value }}</p>{% zone \"z\" %}<i>{{ value }}</i>{% endzone %}'\n\n"
    "@context('value')\n"
    "def value():\n"
    "    return 'sync'\n"
)

_ASYNC_RENDER = (
    "import asyncio\n"
    "from next.pages import context\n\n"
    "async def render(request):\n"
    "    await asyncio.sleep(0)\n"
    "    return '<b>{{ label }}</b>'\n\n"
    "@context('label')\n"
    "async def label():\n"
    "    return 'async'\n"
)

_SLOW_CONTEXT = (
    "import asyncio\n"
    "from next.pages import context\n\n"
    "async def render():\n"
    "    return '<b>{{ label }}</b>'\n\n"
    "@context('label')\n"
    "async def label():\n"
    "    await asyncio.sleep(0.05)\n"
    "    return 'slow'\n"
)

_SYNC_OBJECT = (
    "from django.contrib.auth.models import Permission\n"
    "from next.deps import DObject\n"
    "from next.pages import context\n\n"
    "template = '<p>{{ codename }}</p>'\n\n"
    "@context('codename')\n"
    "def codename(permission: DObject[Permission]):\n"
    "    return permission.codename\n"
)

_ASYNC_REDIRECT = (
    "from django.shortcuts import redirect\n\n"
    "async def render(request):\n"
    "    return redirect('/elsewhere/')\n"
)

_ASYNC_RENDER_SYNC_CONTEXT = (
    "from next.pages import context\n\n"
    "async def render():\n"
    "    return '<b>{{ label }}</b>'\n\n"
    "@context('label')\n"
    "def label():\n"
    "    return 'sync'\n"
)

_ASYNC_POST = (
    "from django.contrib.auth.models import Group\n"
    "from django.db import connection\n"
    "from next.pages import context\n\n"
    "template = '{{ a_made }} {{ b_seen }}'\n\n"
    "@context('a_made')\n"
    "def a_made():\n"
    "    Group.objects.create(name='draft')\n"
    "    return connection.in_atomic_block\n\n"
    "@context('b_seen')\n"
    "async def b_seen(a_made):\n"
    "    group = await Group.objects.aget(name='draft')\n"
    "    return group.name\n"
)


def _async_page(root: Path, source: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    page_file = root / "page.py"
    page_file.write_text(source)
    return page_file


class TestAsyncPages:
    """The sync view drives the async part of a page once, then renders the template."""

    def test_a_static_page_awaits_its_context(self, tmp_path) -> None:
        view = unified_view(page, _async_page(tmp_path, _ASYNC_STATIC))

        response = view(build_page_request())

        assert response.content == (
            b'<p>A AB</p><div data-next-zone="z"><i>ZA</i></div>'
        )

    def test_a_static_page_builds_its_context_through_the_bridge_once(
        self, tmp_path
    ) -> None:
        view = unified_view(page, _async_page(tmp_path, _ASYNC_STATIC))

        with mock.patch(
            "next.pages.manager.run_bridged", wraps=manager_module.run_bridged
        ) as bridged:
            view(build_page_request())

        assert bridged.call_count == 1

    def test_a_zone_get_awaits_the_zone_context(self, tmp_path) -> None:
        view = unified_view(page, _async_page(tmp_path, _ASYNC_STATIC))
        view(build_zone_request("z"))

        with mock.patch.object(page, "awaits", wraps=page.awaits) as awaits:
            response = view(build_zone_request("z"))

        assert envelope_of(response).html_for_zone("z") == (
            '<div data-next-zone="z"><i>ZA</i></div>'
        )
        assert awaits.call_count == 1

    def test_the_render_time_of_an_async_render_includes_its_context(
        self, tmp_path
    ) -> None:
        view = unified_view(page, _async_page(tmp_path, _SLOW_CONTEXT))

        with capture_signals(page_rendered) as recorder:
            view(build_page_request())

        assert recorder.events[0].kwargs["duration_ms"] >= 50

    def test_a_sync_page_never_enters_the_async_path(self, tmp_path) -> None:
        static = unified_view(page, _async_page(tmp_path / "static", _SYNC_STATIC))
        dynamic = unified_view(page, _build_dynamic_page(tmp_path / "dynamic"))

        with (
            mock.patch.object(view_pages, "drive", side_effect=AssertionError),
            mock.patch("next.pages.manager.run_bridged", side_effect=AssertionError),
            mock.patch.object(page, "arender_context", side_effect=AssertionError),
        ):
            full = static(build_page_request())
            zone = static(build_zone_request("z"))
            body = dynamic(build_page_request())

        assert full.content == b'<p>sync</p><div data-next-zone="z"><i>sync</i></div>'
        assert envelope_of(zone).html_for_zone("z") == (
            '<div data-next-zone="z"><i>sync</i></div>'
        )
        assert body.content == b"<p>dynamic</p>"

    @pytest.mark.django_db()
    @pytest.mark.usefixtures("scoped_models")
    def test_a_sync_page_taking_an_object_never_drives(self, tmp_path) -> None:
        permission = Permission.objects.filter(content_type__app_label="auth").first()
        page_file = _async_page(tmp_path, _SYNC_OBJECT)
        view = unified_view(page, page_file)

        with (
            bound_dependency("scope", lambda: "auth"),
            mock.patch.object(view_pages, "drive", side_effect=AssertionError),
            mock.patch("next.pages.manager.run_bridged", side_effect=AssertionError),
        ):
            response = view(build_page_request(), permission=permission.pk)

        assert page.awaits(page_file) is False
        assert response.content == f"<p>{permission.codename}</p>".encode()

    def test_an_async_page_is_driven_once(self, tmp_path) -> None:
        view = unified_view(page, _async_page(tmp_path, _ASYNC_RENDER))

        with mock.patch.object(view_pages, "drive", wraps=view_pages.drive) as driven:
            response = view(build_page_request())

        assert response.content == b"<b>async</b>"
        assert driven.call_count == 1

    def test_an_async_render_with_a_sync_context_collects_it_after_the_drive(
        self, tmp_path
    ) -> None:
        view = unified_view(page, _async_page(tmp_path, _ASYNC_RENDER_SYNC_CONTEXT))

        assert view(build_page_request()).content == b"<b>sync</b>"

    def test_an_async_render_response_bypasses_the_layout(self, tmp_path) -> None:
        view = unified_view(page, _async_page(tmp_path, _ASYNC_REDIRECT))

        response = view(build_page_request())
        tick = view(build_zone_request("z"))

        assert response.status_code == 302
        assert tick.status_code == 302

    def test_a_zone_in_an_async_dynamic_body_is_a_bad_request(self, tmp_path) -> None:
        view = unified_view(page, _async_page(tmp_path, _ASYNC_RENDER))

        response = view(build_zone_request("z"))

        assert response.status_code == 400
        assert response.content == b"zone in dynamic body"

    @pytest.mark.parametrize("source", [_ASYNC_STATIC, _ASYNC_RENDER])
    def test_a_refusal_answers_before_any_drive(self, tmp_path, source) -> None:
        view = unified_view(page, _async_page(tmp_path, source))
        denial = HttpResponse(status=403)

        with (
            mock.patch.object(view_pages, "refusal", return_value=denial),
            mock.patch.object(view_pages, "drive", side_effect=AssertionError),
            mock.patch("next.pages.manager.run_bridged", side_effect=AssertionError),
        ):
            assert view(build_page_request()) is denial

    def test_a_module_broken_at_build_is_served_by_its_colour_now(
        self, tmp_path
    ) -> None:
        healed = _async_page(tmp_path / "sync", "template = '<p>healed</p>'\n")
        driven = _async_page(tmp_path / "async", _ASYNC_RENDER)

        sync_view = view_pages.unified_view(page, healed, None, broken_at_build=True)
        async_view = view_pages.unified_view(page, driven, None, broken_at_build=True)

        assert sync_view(build_page_request()).content == b"<p>healed</p>"
        assert async_view(build_page_request()).content == b"<b>async</b>"

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.usefixtures("_fresh_atomic_declarations")
    def test_an_async_callable_reads_rows_of_the_open_transaction(
        self, tmp_path
    ) -> None:
        view = unified_view(page, _async_page(tmp_path, _ASYNC_POST))

        response = view(_request("POST"))

        assert response.content == b"True draft"
        assert Group.objects.filter(name="draft").exists()


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


def _shown(content: bytes) -> str:
    """Return the page with the per-render boundary id written as `b-x`."""
    return re.sub(r"b-[0-9a-f]+-[0-9]+", "b-x", content.decode())


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

    def test_a_failing_template_renders_the_nearest_error_file(self, bounded) -> None:
        write_page(bounded / "sec" / "leaf", source=_EXPLODING, body="{{ boom }}")

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

    def test_a_failing_context_callable_is_contained(self, bounded) -> None:
        write_page(
            bounded / "sec" / "leaf", source=_FAILING_CONTEXT, body="{{ value }}"
        )

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 500
        assert _shown(response.content) == _SEC_CAUGHT

    def test_a_context_failure_renders_without_context_processors(
        self, bounded, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(manager_module, "_get_context_processors", list)
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

    def test_a_failing_render_is_contained(self, bounded) -> None:
        source = "def render():\n    raise ValueError('render')\n"
        write_page(bounded / "sec" / "leaf", source=source)

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 500
        assert _shown(response.content) == _SEC_CAUGHT

    def test_a_failing_async_render_is_contained(self, bounded) -> None:
        source = "async def render():\n    raise ValueError('render')\n"
        write_page(bounded / "sec" / "leaf", source=source)

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 500
        assert _shown(response.content) == _SEC_CAUGHT

    def test_a_failing_async_context_of_an_async_page_is_contained(
        self, bounded
    ) -> None:
        write_page(bounded / "sec" / "leaf", source=_FAILING_ASYNC_CONTEXT)

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 500
        assert _shown(response.content) == _SEC_CAUGHT

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
        component_template(bounded, "widget", "<b>{{ value }}</b>")
        folder = bounded / "_components" / "widget"
        (folder / "component.py").write_text(_FAILING_WIDGET)
        (folder / "error.djx").write_text("<i>widget {{ error.kind }}</i>")
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

        assert re.sub(r"b-[0-9a-f]+-[0-9]+", "b-x", html) == _SEC_CAUGHT

    def test_a_zone_request_keeps_its_failure(self, bounded) -> None:
        source = "def render():\n    raise ValueError('render')\n"
        page_file = write_page(bounded / "sec" / "leaf", source=source)

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

_FAILING_RENDER = "def render():\n    raise ValueError('render')\n"

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

    def test_a_context_failure_keeps_the_inherited_context_above_the_cut(
        self, navigated
    ) -> None:
        write_page(navigated / "leaf", source=_SECRET_THEN_FAILING, body="{{ value }}")

        response = _get(navigated, "/leaf/")

        assert response.status_code == 500
        assert _shown(response.content).startswith("<html>[NAV]<div")
        assert _caught_body(response.content) == "<p>err [NAV] [] []</p>"

    def test_a_template_failure_gets_the_same_shell(self, navigated) -> None:
        write_page(
            navigated / "leaf",
            source=_SECRET_THEN_EXPLODING,
            body="{{ secret }}{{ boom }}",
        )

        response = _get(navigated, "/leaf/")

        assert _shown(response.content).startswith("<html>[NAV]<div")
        assert _caught_body(response.content) == "<p>err [NAV] [] []</p>"
        assert b"S3CRET" not in response.content

    def test_a_failing_render_runs_the_groups_above_the_cut(self, navigated) -> None:
        write_page(navigated / "leaf", source=_FAILING_RENDER)

        response = _get(navigated, "/leaf/")

        assert _shown(response.content).startswith("<html>[NAV]<div")
        assert _caught_body(response.content) == "<p>err [NAV] [] []</p>"

    def test_a_failing_render_of_an_async_page_runs_them_through_the_bridge(
        self, navigated
    ) -> None:
        write_page(navigated / "leaf", source=_FAILING_RENDER_ASYNC_CONTEXT)

        response = _get(navigated, "/leaf/")

        assert _caught_body(response.content) == "<p>err [NAV] [] []</p>"
        assert b"OWN" not in response.content

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

    def test_a_contained_failure_runs_the_processors_once(
        self, bounded, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[object] = []

        def processor(request: HttpRequest) -> dict[str, object]:
            calls.append(request)
            return {"error": "shadowed"}

        monkeypatch.setattr(
            manager_module, "_get_context_processors", lambda: [processor]
        )
        write_page(bounded / "sec" / "leaf", source=_FAILING_CONTEXT, body="x")

        response = _get(bounded, "/sec/leaf/")

        assert response.status_code == 500
        assert len(calls) == 1

    def test_a_failing_processor_goes_to_django(
        self, bounded, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[object] = []

        def processor(request: HttpRequest) -> dict[str, object]:
            calls.append(request)
            msg = "processor"
            raise RuntimeError(msg)

        monkeypatch.setattr(
            manager_module, "_get_context_processors", lambda: [processor]
        )
        write_page(bounded / "sec" / "leaf", source=_FAILING_CONTEXT, body="x")

        with pytest.raises(RuntimeError, match="processor"):
            _get(bounded, "/sec/leaf/")
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
        html = re.sub(
            r"b-[0-9a-f]+-[0-9]+", "b-x", envelope_of(response).html_for_zone("z")
        )
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

        with mock.patch.object(
            manager_module, "page_boundaries", wraps=manager_module.page_boundaries
        ) as looked:
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


_NAV = '<nav><a href="/">Home</a><a href="/docs/">Docs</a></nav>'
_HOME = '<a href="/" aria-current="page" data-next-current="page">Home</a>'
_DOCS = '<a href="/docs/" aria-current="page" data-next-current="page">Docs</a>'
_PASS_OFF = {"ENABLED": False, "CURRENT": {"ENABLED": False}}


class TestActiveLinks:
    """The active-link pass marks every document and zone the view renders, once."""

    @pytest.fixture()
    def counted(self) -> Iterator[mock.MagicMock]:
        with mock.patch.object(
            CurrentLinks, "mark", autospec=True, side_effect=CurrentLinks.mark
        ) as mark:
            yield mark

    def _document(self, page_instance, tmp_path: Path) -> HttpResponse:
        page_file = tmp_path / "page.py"
        page_file.write_text(f"template = {_NAV!r}")
        response = unified_view(page_instance, page_file)(build_page_request())
        assert isinstance(response, HttpResponse)
        return response

    def test_a_document_is_marked_with_the_router_off(
        self, page_instance, tmp_path, counted
    ) -> None:
        response = self._document(page_instance, tmp_path)
        assert response.content.decode() == _NAV.replace('<a href="/">Home</a>', _HOME)
        assert counted.call_count == 1

    def test_a_routed_document_is_marked_once(
        self, page_instance, tmp_path, counted
    ) -> None:
        with override_next_settings(ROUTER={"ENABLED": True}):
            response = self._document(page_instance, tmp_path)
        assert _HOME in response.content.decode()
        assert counted.call_count == 1

    def test_the_pass_off_leaves_the_bytes(
        self, page_instance, tmp_path, counted
    ) -> None:
        with override_next_settings(ROUTER=_PASS_OFF):
            response = self._document(page_instance, tmp_path)
        assert response.content.decode() == _NAV
        assert counted.call_count == 0

    def test_the_pass_off_leaves_the_bytes_of_an_awaited_page(
        self, page_instance, tmp_path, counted
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(f"async def render(request):\n    return {_NAV!r}\n")
        with override_next_settings(ROUTER=_PASS_OFF):
            response = unified_view(page_instance, page_file)(build_page_request())
        assert response.content.decode() == _NAV
        assert counted.call_count == 0

    @pytest.mark.parametrize(
        ("origin", "marked"),
        [
            pytest.param(None, _HOME, id="zone-marks-its-page"),
            pytest.param("/docs/", _DOCS, id="layer-marks-the-page-beneath"),
        ],
    )
    def test_zone_html_is_marked_for_the_base_page(
        self, page_instance, tmp_path, origin: str | None, marked: str
    ) -> None:
        page_file = build_nested_page(
            tmp_path, body=f'{{% zone "z" %}}{_NAV}{{% endzone %}}'
        )
        request = build_zone_request("z")
        if origin is not None:
            request.META["HTTP_X_NEXT_ORIGIN"] = origin
        response = unified_view(page_instance, page_file)(request)
        assert marked in envelope_of(response).html_for_zone("z")

    def test_zone_html_is_untouched_with_the_pass_off(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(
            tmp_path, body=f'{{% zone "z" %}}{_NAV}{{% endzone %}}'
        )
        with override_next_settings(ROUTER=_PASS_OFF):
            response = unified_view(page_instance, page_file)(build_zone_request("z"))
        assert envelope_of(response).html_for_zone("z") == (
            f'<div data-next-zone="z">{_NAV}</div>'
        )
