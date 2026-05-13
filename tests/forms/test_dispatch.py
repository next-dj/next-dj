import inspect
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect

from next.components.context import component as component_ctx
from next.components.info import ComponentInfo
from next.components.renderers import _inject_component_context
from next.deps import REQUEST_DEP_CACHE_ATTR, Depends, resolver
from next.forms import (
    Form,
    FormActionDispatch,
    FormActionOptions,
    ModelForm,
    RegistryFormActionBackend,
    _get_caller_path,
    form_action_manager,
    page,
)
from next.forms.dispatch import _resolve_form_class
from next.forms.signals import action_dispatched
from next.pages.registry import PageContextRegistry


PAGE_MODULE_FOR_FORM_TESTS = (
    Path(__file__).resolve().parent.parent / "site_pages" / "page.py"
).resolve()


def _run_component_context(tmp_path: Path, request: object) -> object:
    """Register a `@component.context` referencing `Depends("token")` and invoke it.

    Returns the value the consumer wrote under the `out` key.
    """
    module_path = tmp_path / "component.py"
    module_path.write_text("")

    def provide(token: str = Depends("token")) -> str:
        return token

    component_ctx._registry.register(module_path, "out", provide)
    try:
        info = ComponentInfo(
            name="t",
            scope_root=tmp_path,
            scope_relative="t",
            template_path=None,
            module_path=module_path,
            is_simple=False,
        )
        context_data: dict[str, object] = {}
        _inject_component_context(info, context_data, request)
        return context_data["out"]
    finally:
        component_ctx._registry._registry.pop(module_path.resolve(), None)


def _run_page_context(tmp_path: Path, request: object) -> object:
    """Register a `@context` referencing `Depends("token")` and invoke `collect_context`."""
    page_file = tmp_path / "page.py"
    page_file.write_text("")

    def provide(token: str = Depends("token")) -> str:
        return token

    registry = PageContextRegistry()
    registry.register_context(page_file, "out", provide)
    return registry.collect_context(page_file, request).context_data["out"]


class TestFormActionDispatch:
    """FormActionDispatch: _get_caller_path, context_func, ensure_http_response."""

    def test_get_caller_path_raises_when_no_frame(self) -> None:
        """_get_caller_path raises when frame is missing."""
        with pytest.raises(RuntimeError, match="Could not determine caller file path"):
            _get_caller_path(999)

    @pytest.mark.parametrize(
        ("response_val", "kwargs", "expected_status", "assert_extra"),
        [
            (None, {}, 204, None),
            ("hello", {}, 200, lambda r: r.content == b"hello"),
            (
                type("R", (), {"url": "/target/"})(),
                {"request": HttpRequest()},
                302,
                lambda r: r.url == "/target/",
            ),
            (object(), {}, 204, None),
            (type("E", (), {"url": None})(), {}, 204, None),
        ],
        ids=("none_val", "str_val", "redirect_val", "unknown_obj", "empty_url"),
    )
    def test_ensure_http_response_variants(
        self, response_val, kwargs, expected_status, assert_extra
    ) -> None:
        """ensure_http_response: None, str, redirect-like, unknown, empty url."""
        resp = FormActionDispatch.ensure_http_response(response_val, **kwargs)
        assert resp.status_code == expected_status
        if assert_extra is not None:
            assert assert_extra(resp)

    def test_get_caller_path_raises_when_frame_becomes_none(self) -> None:
        """_get_caller_path raises when frame chain ends early."""
        frame = MagicMock()
        frame.f_globals = {"__file__": "/some/path/forms.py"}
        frame.f_back = None
        with (
            patch.object(inspect, "currentframe", return_value=frame),
            pytest.raises(RuntimeError, match="Could not determine caller"),
        ):
            _get_caller_path(0)

    def test_get_caller_path_raises_when_all_frames_are_forms_py(self) -> None:
        """_get_caller_path raises when only forms.py frames exist."""

        def make_frame(f_back: object = None) -> object:
            f = MagicMock()
            f.f_globals = {"__file__": "/some/path/forms.py"}
            f.f_back = f_back
            return f

        chain = None
        for _ in range(15):
            chain = make_frame(chain)
        with (
            patch.object(inspect, "currentframe", return_value=chain),
            pytest.raises(RuntimeError, match="Could not determine caller"),
        ):
            _get_caller_path(0)


@pytest.mark.django_db()
class TestDispatchViaClient:
    """Form action dispatch via Django test client."""

    def test_unknown_uid_returns_404(self, client_no_csrf) -> None:
        """Unknown form uid returns 404."""
        resp = client_no_csrf.get("/_next/form/unknown_uid_12345/")
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "action_name",
        ["test_submit", "test_no_form"],
        ids=("with_form_class", "without_form_class"),
    )
    def test_get_returns_405(self, client_no_csrf, action_name: str) -> None:
        """GET form action URL returns 405 Method Not Allowed."""
        url = form_action_manager.get_action_url(action_name)
        resp = client_no_csrf.get(url)
        assert resp.status_code == 405

    def test_invalid_form_returns_200_with_errors(self, client_no_csrf) -> None:
        """Invalid POST returns 200 with validation errors when _next_form_page is valid."""
        url = form_action_manager.get_action_url("test_submit")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        assert resp.status_code == 200
        c = resp.content
        assert b"error" in c.lower() or b"required" in c.lower() or b"name" in c

    def test_invalid_form_without_next_page_returns_400(self, client_no_csrf) -> None:
        """Invalid POST without _next_form_page returns 400."""
        url = form_action_manager.get_action_url("test_submit")
        resp = client_no_csrf.post(url, data={"name": ""}, follow=False)
        assert resp.status_code == 400

    def test_valid_form_calls_handler(self, client_no_csrf) -> None:
        """Valid POST calls handler and returns 200/204."""
        url = form_action_manager.get_action_url("test_submit")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "Alice",
                "email": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        assert resp.status_code in (200, 204)

    def test_redirect_action_returns_redirect(self, client_no_csrf) -> None:
        """Redirect action returns 302 redirect."""
        url = form_action_manager.get_action_url("test_redirect")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "Bob",
                "email": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        assert resp.status_code == 302
        assert resp.url == "/done/"

    def test_no_form_action_post_returns_200(self, client_no_csrf) -> None:
        """Action without form_class POST returns 200 and body."""
        url = form_action_manager.get_action_url("test_no_form")
        resp = client_no_csrf.post(url, data={})
        assert resp.status_code == 200
        assert b"ok" in resp.content


class TestFormDispatchRenderFragmentBranches:
    """``FormActionDispatch.render_form_fragment`` fallbacks."""

    def test_unknown_action_uses_form_as_p(self, mock_http_request) -> None:
        """Unknown action meta falls back to ``form.as_p()``."""
        backend = RegistryFormActionBackend()

        class F(Form):
            name = django_forms.CharField(max_length=10)

        def h(request: HttpRequest, form: F) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "only", handler=h, options=FormActionOptions(form_class=F)
        )
        req = mock_http_request(method="GET")
        form = F()
        html = FormActionDispatch.render_form_fragment(
            backend,
            req,
            "missing_action",
            form,
            None,
            PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert html == form.as_p()

    def test_empty_template_body_uses_form_as_p(
        self, mock_http_request, tmp_path
    ) -> None:
        """A page.py with no body source and no ancestor layout falls back to ``form.as_p()``."""
        backend = RegistryFormActionBackend()

        class F(Form):
            name = django_forms.CharField(max_length=10)

        def h(request: HttpRequest, form: F) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "frag", handler=h, options=FormActionOptions(form_class=F)
        )
        req = mock_http_request(method="GET")
        form = F()
        blank_page = tmp_path / "page.py"
        blank_page.write_text("")

        html = FormActionDispatch.render_form_fragment(
            backend,
            req,
            "frag",
            form,
            None,
            blank_page,
        )
        assert html == form.as_p()

    def test_dispatch_with_modelform_returning_instance(
        self, mock_http_request
    ) -> None:
        """Test dispatch creates form with instance when ModelForm returns instance."""
        backend = RegistryFormActionBackend()

        # Create a simple mock model
        mock_model = MagicMock()
        mock_model._meta = MagicMock()
        mock_model._meta.get_fields.return_value = []

        class TestModelForm(ModelForm):
            name = django_forms.CharField(max_length=100)

            class Meta:
                model = mock_model
                fields: ClassVar[list[str]] = ["name"]

            @classmethod
            def get_initial(cls, request: HttpRequest) -> object:
                # Return a mock model instance
                mock_instance = MagicMock()
                mock_instance._meta = MagicMock()
                mock_instance._meta.model = mock_model
                return mock_instance

        def handler(
            request: HttpRequest, form: TestModelForm, **_kwargs: object
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action", handler, options=FormActionOptions(form_class=TestModelForm)
        )

        mock_post = MagicMock()
        mock_post.items.return_value = [("name", "test")]
        request = mock_http_request(method="POST", POST=mock_post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Call dispatch - this will create form with instance (line 381)
        response = FormActionDispatch.dispatch(backend, request, "test_action", meta)
        # Should succeed
        assert response.status_code == 302

    @pytest.mark.parametrize(
        "url_param_value",
        [["list", "value"], "not_a_number"],
        ids=["non_string", "string_not_int"],
    )
    def test_dispatch_survives_unusual_url_param_values(
        self, mock_http_request, url_param_value
    ) -> None:
        """`dispatch` accepts url_param values that are not int-convertible strings."""
        backend = RegistryFormActionBackend()

        class TestForm(Form):
            name = django_forms.CharField(max_length=100)

        def handler(
            request: HttpRequest, form: TestForm, **_kwargs: object
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action", handler, options=FormActionOptions(form_class=TestForm)
        )

        mock_post = MagicMock()
        mock_post.items.return_value = [
            ("_url_param_test", url_param_value),
            ("name", "test"),
        ]
        request = mock_http_request(method="POST", POST=mock_post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        response = FormActionDispatch.dispatch(backend, request, "test_action", meta)
        assert response.status_code == 302

    @pytest.mark.parametrize(
        "url_param_value",
        [["list", "value"], "not_a_number"],
        ids=["non_string", "string_not_int"],
    )
    def test_render_form_fragment_survives_unusual_url_param_values(
        self, mock_http_request, url_param_value
    ) -> None:
        """`render_form_fragment` handles url_param values that aren't int-convertible strings."""
        backend = RegistryFormActionBackend()

        class TestForm(Form):
            name = django_forms.CharField(max_length=100)

        def handler(request: HttpRequest, form: TestForm) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action",
            handler,
            options=FormActionOptions(form_class=TestForm),
        )

        mock_post = MagicMock()
        mock_post.items.return_value = [("_url_param_test", url_param_value)]
        request = mock_http_request(POST=mock_post)

        file_path = PAGE_MODULE_FOR_FORM_TESTS
        original_registry = page._template_registry.copy()
        page._template_registry[file_path] = "{{ form.name }}"
        try:
            form = TestForm(initial={"name": "test"})
            html = backend.render_form_fragment(
                request,
                "test_action",
                form,
                template_fragment=None,
                page_file_path=file_path,
            )
            assert isinstance(html, str)
        finally:
            page._template_registry.clear()
            page._template_registry.update(original_registry)

    def test_dispatch_with_form_without_get_initial(self, mock_http_request) -> None:
        """Test that dispatch raises TypeError when form class doesn't have get_initial."""
        backend = RegistryFormActionBackend()

        # Create a form class that doesn't inherit from BaseForm
        class CustomDjangoForm(django_forms.Form):
            name = django_forms.CharField(max_length=100)

        def handler(
            request: HttpRequest, form: CustomDjangoForm
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action",
            handler,
            options=FormActionOptions(form_class=CustomDjangoForm),
        )

        post = MagicMock()
        post.items.return_value = []
        request = mock_http_request(method="POST", POST=post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will trigger the error
        with pytest.raises(TypeError, match="must have get_initial method"):
            FormActionDispatch.dispatch(backend, request, "test_action", meta)

    def test_dispatch_with_form_returning_instance_but_not_modelform(
        self, mock_http_request
    ) -> None:
        """Test that dispatch raises TypeError when Form returns instance but isn't ModelForm."""
        backend = RegistryFormActionBackend()

        class CustomForm(Form):
            name = django_forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest) -> object:
                # Return a mock model instance
                mock_instance = MagicMock()
                mock_instance._meta = MagicMock()
                mock_instance._meta.model = MagicMock()
                return mock_instance

        def handler(request: HttpRequest, form: CustomForm) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action", handler, options=FormActionOptions(form_class=CustomForm)
        )

        post = MagicMock()
        post.items.return_value = []
        request = mock_http_request(method="POST", POST=post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        # Real call to dispatch - this will trigger the error
        with pytest.raises(
            TypeError, match="instance parameter only supported for ModelForm"
        ):
            FormActionDispatch.dispatch(backend, request, "test_action", meta)


class TestResolveFormClass:
    """`_resolve_form_class`: type passthrough, factory call, error paths."""

    def test_form_class_type_returned_as_is(self, mock_http_request) -> None:
        """A `Form` subclass is returned unchanged without DI resolution."""

        class F(Form):
            name = django_forms.CharField(max_length=10)

        request = mock_http_request(method="POST")
        out = _resolve_form_class(F, request, {})
        assert out is F

    def test_factory_callable_resolves_per_request(self, mock_http_request) -> None:
        """A callable factory is invoked with DI-resolved kwargs each call."""

        class F(Form):
            name = django_forms.CharField(max_length=10)

        seen: dict[str, object] = {}

        def factory(request: HttpRequest, model_name: str) -> type[Form]:
            seen["request"] = request
            seen["model_name"] = model_name
            return F

        request = mock_http_request(method="POST")
        out = _resolve_form_class(
            factory,
            request,
            {"model_name": "tag"},
        )
        assert out is F
        assert seen["model_name"] == "tag"
        assert seen["request"] is request

    def test_non_callable_raises_typeerror(self, mock_http_request) -> None:
        """An object that is neither a type nor callable is a configuration error."""
        request = mock_http_request(method="POST")
        with pytest.raises(TypeError, match="Form subclass or callable"):
            _resolve_form_class("not-a-form", request, {})

    def test_factory_returning_non_type_raises(self, mock_http_request) -> None:
        """A factory must return a class; returning an instance is a hard error."""

        def bad_factory(request: HttpRequest) -> object:
            return "not-a-class"

        request = mock_http_request(method="POST")
        with pytest.raises(TypeError, match="factory must return a Form subclass"):
            _resolve_form_class(bad_factory, request, {})


class TestDispatchSharedDepCache:
    """Dispatch threads a single dep_cache through factory, handler, and signals."""

    def test_dispatch_attaches_dep_cache_to_request(self, mock_http_request) -> None:
        """``FormActionDispatch.dispatch`` sets ``request._next_dep_cache`` to a dict."""
        backend = RegistryFormActionBackend()

        def handler() -> str:
            return "ok"

        backend.register_action("only_handler", handler, options=FormActionOptions())

        mock_post = MagicMock()
        mock_post.items.return_value = []
        request = mock_http_request(method="POST", POST=mock_post)

        meta = backend.get_meta("only_handler")
        assert meta is not None
        FormActionDispatch.dispatch(backend, request, "only_handler", meta)

        assert isinstance(getattr(request, REQUEST_DEP_CACHE_ATTR, None), dict)

    def test_action_dispatched_signal_carries_dep_cache(
        self, mock_http_request
    ) -> None:
        """``action_dispatched`` payload includes a ``dep_cache`` snapshot."""

        @resolver.dependency("greeting")
        def greeting() -> str:
            return "hi"

        backend = RegistryFormActionBackend()

        def handler(greeting: str = Depends("greeting")) -> str:
            return greeting

        backend.register_action("dep_handler", handler, options=FormActionOptions())

        seen: dict[str, object] = {}

        def receiver(**kwargs: object) -> None:
            seen.update(kwargs)

        action_dispatched.connect(receiver)
        try:
            mock_post = MagicMock()
            mock_post.items.return_value = []
            request = mock_http_request(method="POST", POST=mock_post)
            meta = backend.get_meta("dep_handler")
            assert meta is not None
            FormActionDispatch.dispatch(backend, request, "dep_handler", meta)
        finally:
            action_dispatched.disconnect(receiver)
            resolver._dependency_callables.pop("greeting", None)

        assert "dep_cache" in seen
        assert isinstance(seen["dep_cache"], dict)
        assert seen["dep_cache"].get("greeting") == "hi"

    def test_depends_provider_invoked_once_across_phases(
        self, mock_http_request
    ) -> None:
        """A ``Depends("name")`` shared by factory and handler resolves exactly once.

        The dispatcher threads a single ``dep_cache`` through
        ``_resolve_form_class``, ``form.get_initial``, and the handler
        (see ``FormActionDispatch._dispatch_with_form``). The resolver's
        named-dependency cache keys on the ``Depends`` name, so any
        subsequent phase that asks for the same name hits the cache.
        """
        calls = {"n": 0}

        @resolver.dependency("widget")
        def make_widget() -> str:
            calls["n"] += 1
            return "w"

        class WForm(Form):
            name = django_forms.CharField(max_length=10, required=False)

        def factory(widget: str = Depends("widget")) -> type[Form]:
            assert widget == "w"
            return WForm

        def handler_like(widget: str = Depends("widget")) -> str:
            return widget

        request = mock_http_request(method="POST")
        dep_cache: dict[str, object] = {}
        dep_stack: list[str] = []

        try:
            cls = _resolve_form_class(factory, request, {}, dep_cache, dep_stack)
            assert cls is WForm
            resolved = resolver.resolve_dependencies(
                handler_like,
                request=request,
                _cache=dep_cache,
                _stack=dep_stack,
            )
        finally:
            resolver._dependency_callables.pop("widget", None)

        assert resolved["widget"] == "w"
        assert calls["n"] == 1

    @pytest.mark.parametrize(
        "run_consumer",
        [_run_component_context, _run_page_context],
        ids=["component_context", "page_context"],
    )
    def test_request_scope_cache_rejoined_by_consumer(
        self, mock_http_request, tmp_path, run_consumer
    ) -> None:
        """Re-render consumers reuse ``request._next_dep_cache`` instead of re-resolving."""
        calls = {"n": 0}

        @resolver.dependency("token")
        def make_token() -> str:
            calls["n"] += 1
            return "fresh"

        try:
            request = mock_http_request(method="POST")
            setattr(request, REQUEST_DEP_CACHE_ATTR, {"token": "preloaded"})
            value = run_consumer(tmp_path, request)
        finally:
            resolver._dependency_callables.pop("token", None)

        assert value == "preloaded"
        assert calls["n"] == 0
