from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django import forms
from django.contrib.auth.models import Group
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict

from next.deps.objects import SCOPE_DEPENDENCY
from next.forms import Form
from next.forms.backends import ActionRegistration, RegistryFormActionBackend
from next.forms.dispatch import FormActionDispatch
from next.forms.dispatch.build import _form_action_context_callable
from next.forms.dispatch.responses import _normalize_handler_response
from next.forms.origin import (
    _ORIGIN_MATCH_ATTR,
    filter_reserved_url_kwargs,
    resolve_origin,
)
from next.forms.uid import ORIGIN_FIELD_NAME, validated_origin_path
from tests.benchmarks.factories import StandInRows
from tests.support import PermissionHolder, bound_dependency, routed
from tests.support.access import write_proxy
from tests.support.helpers import build_mock_http_request


if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from next.forms.backends import ActionMeta


class _RedirectDuck:
    """Plain `.url` carrier so the bench measures the duck path, not MagicMock."""

    url = "/redirect-target/"


class TestBenchDispatchHelpers:
    @pytest.mark.benchmark(group="forms.dispatch")
    def test_normalize_none(self, benchmark) -> None:
        benchmark(_normalize_handler_response, None)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_normalize_httpresponse(self, benchmark) -> None:
        response = HttpResponse("ok")
        benchmark(_normalize_handler_response, response)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_normalize_str(self, benchmark) -> None:
        benchmark(_normalize_handler_response, "hello world")

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_normalize_redirect_duck(self, benchmark) -> None:
        raw = _RedirectDuck()
        benchmark(_normalize_handler_response, raw)
        assert isinstance(_normalize_handler_response(raw), HttpResponseRedirect)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_filter_reserved_url_kwargs(self, benchmark) -> None:
        payload = {f"k_{i}": i for i in range(30)}
        payload.update({"request": 1, "form": 2})
        benchmark(filter_reserved_url_kwargs, payload)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_resolve_origin_cold(self, benchmark) -> None:
        # A fixed urlconf measures the resolver, not the live test site page count.
        request = build_mock_http_request(
            method="POST",
            POST={"_next_form_origin": "/items/42/"},
            urlconf="tests.benchmarks.forms.urls_bench",
        )

        def run() -> object:
            if hasattr(request, _ORIGIN_MATCH_ATTR):
                delattr(request, _ORIGIN_MATCH_ATTR)
            return resolve_origin(request)

        match = benchmark(run)
        assert match is not None
        assert match.url_kwargs == {"id": 42}

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_resolve_origin_memoised(self, benchmark) -> None:
        request = build_mock_http_request(
            method="POST",
            POST={"_next_form_origin": "/items/42/"},
            urlconf="tests.benchmarks.forms.urls_bench",
        )
        first = resolve_origin(request)
        match = benchmark(resolve_origin, request)
        assert match is first


class TestBenchOriginValidation:
    @pytest.mark.benchmark(group="forms.origin")
    def test_validated_origin_path(self, benchmark) -> None:
        assert benchmark(validated_origin_path, "/items/42/?q=x") == "/items/42/?q=x"

    @pytest.mark.benchmark(group="forms.origin")
    def test_oversize_protocol_relative_value_refuses(self, benchmark) -> None:
        """Refused on its length alone, so a megabyte value costs no scan."""
        assert benchmark(validated_origin_path, "//" + "a" * 2_400_000) is None


class _BenchForm(Form):
    """Minimal form used by the end-to-end dispatch benches."""

    name = forms.CharField(max_length=32)


def _ok_handler(**kwargs) -> HttpResponseRedirect:
    return HttpResponseRedirect("/")


class TestBenchDispatchEndToEnd:
    @pytest.mark.benchmark(group="forms.dispatch")
    def test_dispatch_valid_form(self, benchmark) -> None:
        """Valid submission, so the handler runs and a redirect is emitted."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="bench_action",
                file_path=__file__,
                scope="shared",
                handler=_ok_handler,
                form_class=_BenchForm,
            )
        )
        meta = backend.get_meta("bench_action")
        assert meta is not None
        post = QueryDict("name=bench")
        request = build_mock_http_request(method="POST", POST=post, FILES=None)
        response = benchmark(
            FormActionDispatch.dispatch, backend, request, "bench_action", meta
        )
        assert isinstance(response, HttpResponseRedirect)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_dispatch_invalid_form(self, benchmark) -> None:
        """Invalid submission, so validation_failed fires with an errors payload."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="bench_action",
                file_path=__file__,
                scope="shared",
                handler=_ok_handler,
                form_class=_BenchForm,
            )
        )
        meta = backend.get_meta("bench_action")
        assert meta is not None
        # The required name is missing, so the dispatch takes the error path.
        post = QueryDict()
        request = build_mock_http_request(method="POST", POST=post, FILES=None)
        response = benchmark(
            FormActionDispatch.dispatch, backend, request, "bench_action", meta
        )
        assert not isinstance(response, HttpResponseRedirect)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_dispatch_unguarded_form_no_hook_overhead(self, benchmark) -> None:
        """A no-hook form pays no permission-hook resolve on the dispatch path.

        Pins the zero-overhead promise: an undeclared check_permissions or
        has_object_permission costs two ClassVar reads, never a resolver call.
        """
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="bench_action",
                file_path=__file__,
                scope="shared",
                handler=_ok_handler,
                form_class=_BenchForm,
            )
        )
        meta = backend.get_meta("bench_action")
        assert meta is not None
        assert _BenchForm._has_check_permissions is False
        assert _BenchForm._has_object_permission is False
        post = QueryDict("name=bench")
        request = build_mock_http_request(method="POST", POST=post, FILES=None)
        response = benchmark(
            FormActionDispatch.dispatch, backend, request, "bench_action", meta
        )
        assert isinstance(response, HttpResponseRedirect)

    @pytest.mark.benchmark(group="forms.dispatch")
    def test_dispatch_through_subclassed_backend(self, benchmark) -> None:
        """Dispatch through a thin `RegistryFormActionBackend` subclass.

        Pins the wrapper overhead for projects that inherit from the registry backend.
        """

        class _SubclassedBackend(RegistryFormActionBackend):
            def dispatch(self, request, uid):  # type: ignore[override]
                return super().dispatch(request, uid)

        backend = _SubclassedBackend()
        backend.register_action(
            ActionRegistration(
                name="bench_action",
                file_path=__file__,
                scope="shared",
                handler=_ok_handler,
                form_class=_BenchForm,
            )
        )
        meta = backend.get_meta("bench_action")
        assert meta is not None
        post = QueryDict("name=bench")
        request = build_mock_http_request(method="POST", POST=post, FILES=None)
        response = benchmark(
            FormActionDispatch.dispatch, backend, request, "bench_action", meta
        )
        assert isinstance(response, HttpResponseRedirect)


_BUILD_KWARGS: dict[str, object] = {"prefix": "bench", "initial": {"name": "bench"}}


class _KwargsHookBenchForm(Form):
    """The bench form again, its constructor kwargs coming from the hook."""

    name = forms.CharField(max_length=32)

    @classmethod
    def get_form_kwargs(cls, request: HttpRequest) -> dict[str, object]:
        return dict(_BUILD_KWARGS)


def _bench_form_factory() -> tuple[type[Form], dict[str, object]]:
    return _BenchForm, dict(_BUILD_KWARGS)


class TestBenchFormBuild:
    """One render-time form build per round, through each constructor kwargs source.

    Both builds yield the same prefixed form, so the two numbers compare evenly.
    """

    @pytest.mark.benchmark(group="forms.build")
    def test_build_through_get_form_kwargs(self, benchmark) -> None:
        build = _form_action_context_callable(_KwargsHookBenchForm)
        request = build_mock_http_request(method="GET")
        namespace = benchmark(build, request)
        assert namespace.form.prefix == "bench"
        assert namespace.form.initial == {"name": "bench"}

    @pytest.mark.benchmark(group="forms.build")
    def test_build_through_factory_function(self, benchmark) -> None:
        build = _form_action_context_callable(_bench_form_factory)
        request = build_mock_http_request(method="GET")
        namespace = benchmark(build, request)
        assert namespace.form.prefix == "bench"
        assert namespace.form.initial == {"name": "bench"}

    @pytest.mark.benchmark(group="forms.build")
    def test_build_through_get_initial(self, benchmark) -> None:
        """The form without the hook override keeps the plain `get_initial` path."""
        build = _form_action_context_callable(_BenchForm)
        request = build_mock_http_request(method="GET")
        assert _BenchForm._has_get_form_kwargs is False
        namespace = benchmark(build, request)
        assert namespace.form.initial == {}


_TREE_PROXY = "proxy.login_required()\n"
_TREE_GROUP_PK = 7
_TREE_PAGES = {
    "open": "x = 1\n",
    "guarded": (
        'from next.pages import page\n\npage.permission_required("auth.change_user")\n'
    ),
    "guarded-object": (
        "from django.contrib.auth.models import Group\n\n"
        "from next.deps import DObject\n"
        "from next.pages import page\n\n"
        'page.permission_required("auth.change_user", obj=DObject[Group])\n'
    ),
}


@pytest.fixture()
def stand_in_scope(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Answer every `Group` lookup from memory and bind the `scope` dependency."""
    rows = StandInRows(Group(pk=_TREE_GROUP_PK, name="bench"))
    monkeypatch.setattr(
        Group._default_manager, "visible_to", rows.visible_to, raising=False
    )
    with bound_dependency(SCOPE_DEPENDENCY, lambda: "tenant"):
        yield


def _tree_action(
    root: Path, shape: str
) -> tuple[RegistryFormActionBackend, ActionMeta]:
    """Register the bench action as declared by a `page.py` of a routed tree.

    Every shape but `open` adds a `proxy.py` requiring a login above the page.
    """
    directory = root / "guarded" / "[int:group]"
    directory.mkdir(parents=True)
    if shape != "open":
        write_proxy(root, _TREE_PROXY)
    page_file = directory / "page.py"
    page_file.write_text(_TREE_PAGES[shape])
    (directory / "template.djx").write_text("<p>bench</p>\n")
    backend = RegistryFormActionBackend()
    backend.register_action(
        ActionRegistration(
            name="bench_action",
            file_path=str(page_file),
            scope="shared",
            handler=_ok_handler,
            form_class=_BenchForm,
        )
    )
    meta = backend.get_meta("bench_action")
    assert meta is not None
    return backend, meta


@pytest.mark.usefixtures("stand_in_scope")
class TestBenchDispatchUnderTreeAccess:
    """A valid dispatch of an action whose `page.py` sits under a page tree rule."""

    @pytest.mark.parametrize("shape", list(_TREE_PAGES))
    @pytest.mark.benchmark(group="forms.dispatch.access")
    def test_dispatch_under_the_owner_rule(
        self, tmp_path: Path, shape: str, benchmark
    ) -> None:
        root = tmp_path / "pages"
        backend, meta = _tree_action(root, shape)
        origin = f"/guarded/{_TREE_GROUP_PK}/"
        request = build_mock_http_request(
            method="POST",
            POST=QueryDict(f"name=bench&{ORIGIN_FIELD_NAME}={origin}"),
            FILES=None,
        )
        request.user = PermissionHolder("auth.change_user")
        with routed(root):
            response = FormActionDispatch.dispatch(
                backend, request, "bench_action", meta
            )
            assert resolve_origin(request) is not None
            assert isinstance(response, HttpResponseRedirect)
            assert response.url == "/"
            assert bool(request.user.asked) is (shape != "open")
            benchmark(
                FormActionDispatch.dispatch, backend, request, "bench_action", meta
            )
