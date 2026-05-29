import inspect
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from django import forms as django_forms
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpRequest, HttpResponseRedirect, QueryDict

from next.components.context import component as component_ctx
from next.components.info import ComponentInfo
from next.components.renderers import _inject_component_context
from next.deps import REQUEST_DEP_CACHE_ATTR, Depends, resolver
from next.forms import (
    Form,
    FormActionDispatch,
    ModelForm,
    RegistryFormActionBackend,
    form_action_manager,
    page,
)
from next.forms.dispatch import (
    _bind_form_for_post,
    _form_action_context_callable,
    _form_from_initial_data,
    _get_caller_path,
    _resolve_form_class,
)
from next.forms.manager import build_form_namespace_for_action
from next.forms.signals import action_dispatched
from next.forms.wizard import FormWizard
from next.pages.registry import PageContextRegistry


PAGE_MODULE_FOR_FORM_TESTS = (
    Path(__file__).resolve().parent.parent / "site_pages" / "page.py"
).resolve()

_FAKE_FILE = "/fake/myapp/forms.py"


class WizardIdentityStep(Form):
    """First step of the dispatch wizard."""

    name = django_forms.CharField(max_length=100)


class WizardScopeStep(Form):
    """Second step of the dispatch wizard."""

    scope = django_forms.CharField(max_length=100)


class WizardExtraStep(Form):
    """Conditional step toggled by earlier answers."""

    extra = django_forms.CharField(max_length=100)


class DispatchWizard(FormWizard):
    """Two-step wizard exercised through HTTP dispatch."""

    class Meta:
        """Two ordered steps routed through the wizard backend."""

        steps: ClassVar = [
            ("identity", WizardIdentityStep),
            ("scope", WizardScopeStep),
        ]

    done_payloads: ClassVar[list] = []

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Record the merged cleaned data and redirect to a thank-you page."""
        type(self).done_payloads.append(cleaned_data)
        return HttpResponseRedirect("/thanks/")


class ConditionalDispatchWizard(FormWizard):
    """Wizard that inserts a step based on the first step's answer."""

    class Meta:
        """Two declared steps that a steps_for override can expand."""

        steps: ClassVar = [
            ("identity", WizardIdentityStep),
            ("scope", WizardScopeStep),
        ]

    done_payloads: ClassVar[list] = []

    def steps_for(self) -> list:
        """Insert an extra step when the name asks for it."""
        base = [("identity", WizardIdentityStep), ("scope", WizardScopeStep)]
        if self.cleaned_data_so_far().get("name") == "needs-extra":
            base.insert(1, ("extra", WizardExtraStep))
        return base

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Record the merged cleaned data and redirect."""
        type(self).done_payloads.append(cleaned_data)
        return HttpResponseRedirect("/thanks/")


class KwargsDispatchWizard(FormWizard):
    """Wizard that feeds a prefix to its step form through get_form_kwargs."""

    class Meta:
        """One step routed through the wizard backend."""

        steps: ClassVar = [("identity", WizardIdentityStep)]

    seen_kwargs: ClassVar[list] = []

    def get_form_kwargs(self) -> dict:
        """Pass a prefix and record the cross-step inputs it was given."""
        type(self).seen_kwargs.append(
            (self.current_step(), dict(self.cleaned_data_so_far()))
        )
        return {"prefix": "wiz"}

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Redirect once the only step validates."""
        return HttpResponseRedirect("/thanks/")


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
        ["simple_form", "test_no_form"],
        ids=("with_form_class", "without_form_class"),
    )
    def test_get_returns_405(self, client_no_csrf, action_name: str) -> None:
        """GET form action URL returns 405 Method Not Allowed."""
        url = form_action_manager.get_action_url(action_name)
        resp = client_no_csrf.get(url)
        assert resp.status_code == 405

    def test_invalid_form_returns_200_with_errors(self, client_no_csrf) -> None:
        """Invalid POST returns 200 with validation errors when _next_form_page is valid."""
        url = form_action_manager.get_action_url("simple_form")
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
        url = form_action_manager.get_action_url("simple_form")
        resp = client_no_csrf.post(url, data={"name": ""}, follow=False)
        assert resp.status_code == 400

    def test_valid_form_calls_on_valid(self, client_no_csrf) -> None:
        """Valid POST calls on_valid and returns appropriate response."""
        url = form_action_manager.get_action_url("simple_form")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "Alice",
                "email": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
                "_next_form_origin": "/",
            },
            follow=False,
        )
        # SimpleForm.on_valid returns None which triggers redirect_to_origin
        assert resp.status_code in (200, 204, 302)

    def test_redirect_action_returns_redirect(self, client_no_csrf) -> None:
        """Redirect action returns 302 redirect."""
        url = form_action_manager.get_action_url("simple_form_redirect")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "Bob",
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

    def test_on_valid_returning_none_uses_form_response(self, client_no_csrf) -> None:
        """on_valid returning None with a valid _next_form_page re-renders the page."""
        url = form_action_manager.get_action_url("simple_form")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "Alice",
                "email": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        # None → ensure_http_response → form_response → 200
        assert resp.status_code == 200


class TestFormDispatchRenderFragmentBranches:
    """``FormActionDispatch.render_form_fragment`` fallbacks."""

    def test_unknown_action_uses_form_fallback(self, mock_http_request) -> None:
        """Unknown action meta falls back to the version-safe bare-form render."""
        backend = RegistryFormActionBackend()

        class F(Form):
            name = django_forms.CharField(max_length=10)

        def h(request: HttpRequest, form: F) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "only",
            handler=h,
            file_path=_FAKE_FILE,
            scope="shared",
        )
        req = mock_http_request(method="GET")
        form = F()
        html = FormActionDispatch.render_form_fragment(
            backend,
            req,
            "missing_action",
            form,
            PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert html == form.render(form.template_name_p)

    def test_empty_template_body_uses_form_fallback(
        self, mock_http_request, tmp_path
    ) -> None:
        """A page.py without body and no ancestor layout falls back to a bare render."""
        backend = RegistryFormActionBackend()

        class F(Form):
            name = django_forms.CharField(max_length=10)

        def h(request: HttpRequest, form: F) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "frag",
            handler=h,
            file_path=_FAKE_FILE,
            scope="shared",
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
            blank_page,
        )
        assert html == form.render(form.template_name_p)

    def test_dispatch_with_modelform_returning_instance(
        self, mock_http_request
    ) -> None:
        """Dispatch creates form with instance when ModelForm returns instance."""
        backend = RegistryFormActionBackend()

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
                mock_instance = MagicMock()
                mock_instance._meta = MagicMock()
                mock_instance._meta.model = mock_model
                return mock_instance

        def handler(
            request: HttpRequest, form: TestModelForm, **_kwargs: object
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action",
            handler=handler,
            form_class=TestModelForm,
            file_path=_FAKE_FILE,
            scope="shared",
        )

        mock_post = MagicMock()
        mock_post.items.return_value = [("name", "test")]
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
            "test_action",
            handler=handler,
            form_class=TestForm,
            file_path=_FAKE_FILE,
            scope="shared",
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
            handler=handler,
            form_class=TestForm,
            file_path=_FAKE_FILE,
            scope="shared",
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
        """Dispatch raises TypeError when form class doesn't have get_initial."""
        backend = RegistryFormActionBackend()

        class CustomDjangoForm(django_forms.Form):
            name = django_forms.CharField(max_length=100)

        def handler(
            request: HttpRequest, form: CustomDjangoForm
        ) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action",
            handler=handler,
            form_class=CustomDjangoForm,
            file_path=_FAKE_FILE,
            scope="shared",
        )

        post = MagicMock()
        post.items.return_value = []
        request = mock_http_request(method="POST", POST=post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        with pytest.raises(TypeError, match="must have get_initial method"):
            FormActionDispatch.dispatch(backend, request, "test_action", meta)

    def test_dispatch_with_form_returning_instance_but_not_modelform(
        self, mock_http_request
    ) -> None:
        """Dispatch raises TypeError when Form returns instance but isn't ModelForm."""
        backend = RegistryFormActionBackend()

        class CustomForm(Form):
            name = django_forms.CharField(max_length=100)

            @classmethod
            def get_initial(cls, request: HttpRequest) -> object:
                mock_instance = MagicMock()
                mock_instance._meta = MagicMock()
                mock_instance._meta.model = MagicMock()
                return mock_instance

        def handler(request: HttpRequest, form: CustomForm) -> HttpResponseRedirect:
            return HttpResponseRedirect("/")

        backend.register_action(
            "test_action",
            handler=handler,
            form_class=CustomForm,
            file_path=_FAKE_FILE,
            scope="shared",
        )

        post = MagicMock()
        post.items.return_value = []
        request = mock_http_request(method="POST", POST=post, FILES=None)

        meta = backend.get_meta("test_action")
        assert meta is not None

        with pytest.raises(
            TypeError, match="instance parameter only supported for ModelForm"
        ):
            FormActionDispatch.dispatch(backend, request, "test_action", meta)

    def test_dispatch_no_form_class_no_handler_returns_400(
        self, mock_http_request
    ) -> None:
        """Dispatch with no form_class and no handler returns 400."""
        backend = RegistryFormActionBackend()
        post = MagicMock()
        post.items.return_value = []
        request = mock_http_request(method="POST", POST=post, FILES=None)

        # Construct meta manually without form_class or handler.
        meta = {
            "handler": None,
            "form_class": None,
            "uid": "fake_uid",
            "file_path": _FAKE_FILE,
            "scope": "shared",
        }
        response = FormActionDispatch.dispatch(backend, request, "no_op", meta)
        assert response.status_code == 400


class TestDispatchOnValid:
    """Dispatch calls on_valid when handler is None (class-based form)."""

    def test_on_valid_returning_redirect_gives_redirect(
        self, mock_http_request
    ) -> None:
        """on_valid returning HttpResponseRedirect is forwarded as 302."""
        backend = RegistryFormActionBackend()

        class RedirectForm(Form):
            name = django_forms.CharField(max_length=100)

            def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
                return HttpResponseRedirect("/redirected/")

        backend.register_action(
            "redirect_form",
            form_class=RedirectForm,
            file_path=_FAKE_FILE,
            scope="shared",
        )

        mock_post = MagicMock()
        mock_post.items.return_value = [("name", "Alice")]
        request = mock_http_request(method="POST", POST=mock_post, FILES=None)

        meta = backend.get_meta("redirect_form")
        assert meta is not None

        response = FormActionDispatch.dispatch(backend, request, "redirect_form", meta)
        assert response.status_code == 302
        assert response.url == "/redirected/"

    def test_on_valid_returning_none_gives_400_without_next_page(
        self, mock_http_request
    ) -> None:
        """on_valid returning None without _next_form_page gives 400 bad request."""
        backend = RegistryFormActionBackend()

        class NoneForm(Form):
            name = django_forms.CharField(max_length=100)

            def on_valid(self, request: HttpRequest) -> None:
                return None

        backend.register_action(
            "none_form",
            form_class=NoneForm,
            file_path=_FAKE_FILE,
            scope="shared",
        )

        mock_post = MagicMock()
        mock_post.items.return_value = [("name", "Alice")]
        request = mock_http_request(method="POST", POST=mock_post, FILES=None)

        meta = backend.get_meta("none_form")
        assert meta is not None

        # None → ensure_http_response(None, request, action_name, backend)
        # → form_response → validated_next_form_page_path → None → 400
        response = FormActionDispatch.dispatch(backend, request, "none_form", meta)
        assert response.status_code == 400


class TestResolveFormClass:
    """`_resolve_form_class`: type passthrough, factory call, error paths."""

    def test_form_class_type_returned_as_is(self, mock_http_request) -> None:
        """A `Form` subclass returns `(cls, {})` without DI resolution."""

        class F(Form):
            name = django_forms.CharField(max_length=10)

        request = mock_http_request(method="POST")
        cls, init_kwargs = _resolve_form_class(F, request, {})
        assert cls is F
        assert init_kwargs == {}

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
        cls, init_kwargs = _resolve_form_class(
            factory,
            request,
            {"model_name": "tag"},
        )
        assert cls is F
        assert init_kwargs == {}
        assert seen["model_name"] == "tag"
        assert seen["request"] is request

    def test_factory_can_return_class_and_init_kwargs(self, mock_http_request) -> None:
        """A factory returning `(cls, init_kwargs)` propagates kwargs through."""

        class F(Form):
            name = django_forms.CharField(max_length=10)

        def factory(request: HttpRequest) -> tuple[type[Form], dict[str, object]]:
            return F, {"prefix": "x", "use_required_attribute": False}

        request = mock_http_request(method="POST")
        cls, init_kwargs = _resolve_form_class(factory, request, {})
        assert cls is F
        assert init_kwargs == {"prefix": "x", "use_required_attribute": False}

    def test_non_callable_raises_typeerror(self, mock_http_request) -> None:
        """An object that is neither a type nor callable is a configuration error."""
        request = mock_http_request(method="POST")
        with pytest.raises(TypeError, match="Form subclass or callable"):
            _resolve_form_class("not-a-form", request, {})

    def test_factory_returning_non_type_raises(self, mock_http_request) -> None:
        """A factory must return a class or `(cls, init_kwargs)`."""

        def bad_factory(request: HttpRequest) -> object:
            return "not-a-class"

        request = mock_http_request(method="POST")
        with pytest.raises(TypeError, match="factory must return a Form subclass"):
            _resolve_form_class(bad_factory, request, {})


class _CustomInitForm(django_forms.Form):
    """Form whose constructor takes extra kwargs (mimicking AuthenticationForm)."""

    name = django_forms.CharField(max_length=10, required=False)

    def __init__(
        self,
        *args: object,
        label_suffix: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.label_suffix = label_suffix


class TestFormClassInitKwargs:
    """Factory returning `(cls, init_kwargs)` bypasses get_initial."""

    @pytest.mark.parametrize(
        ("bound", "suffix"),
        [(False, "?"), (True, "!")],
        ids=("unbound", "bound"),
    )
    def test_form_built_with_init_kwargs(
        self, mock_http_request, bound, suffix
    ) -> None:
        if bound:
            mock_post = MagicMock()
            mock_post.get.return_value = "ok"
            request = mock_http_request(method="POST", POST=mock_post, FILES=None)
            form = _bind_form_for_post(
                _CustomInitForm, request, None, init_kwargs={"label_suffix": suffix}
            )
        else:
            form = _form_from_initial_data(
                _CustomInitForm, None, init_kwargs={"label_suffix": suffix}
            )
        assert isinstance(form, _CustomInitForm)
        assert form.label_suffix == suffix

    def test_context_callable_uses_init_kwargs(self, mock_http_request) -> None:
        def factory(request: HttpRequest) -> tuple[type[django_forms.Form], dict]:
            return _CustomInitForm, {"label_suffix": "@"}

        ctx_func = _form_action_context_callable(factory)
        request = mock_http_request(method="GET")
        ns = ctx_func(request)
        assert isinstance(ns.form, _CustomInitForm)
        assert ns.form.label_suffix == "@"

    def test_dispatch_uses_init_kwargs_and_skips_get_initial(
        self, mock_http_request
    ) -> None:
        def factory(
            request: HttpRequest,
        ) -> tuple[type[django_forms.Form], dict]:
            return _CustomInitForm, {"label_suffix": "$"}

        captured: dict[str, object] = {}

        def handler(form: _CustomInitForm) -> HttpResponseRedirect:
            captured["form"] = form
            return HttpResponseRedirect("/")

        backend = RegistryFormActionBackend()
        backend.register_action(
            "init_kwargs_action",
            handler=handler,
            form_class=factory,
            file_path=_FAKE_FILE,
            scope="shared",
        )

        post = QueryDict(mutable=True)
        post["name"] = "x"
        request = mock_http_request(
            method="POST",
            POST=post,
            FILES=QueryDict(),
        )
        meta = backend.get_meta("init_kwargs_action")
        assert meta is not None
        response = FormActionDispatch.dispatch(
            backend, request, "init_kwargs_action", meta
        )
        assert response.status_code == 302
        bound = captured["form"]
        assert isinstance(bound, _CustomInitForm)
        assert bound.label_suffix == "$"


class TestDispatchSharedDepCache:
    """Dispatch threads a single dep_cache through factory, handler, and signals."""

    def test_dispatch_attaches_dep_cache_to_request(self, mock_http_request) -> None:
        """``FormActionDispatch.dispatch`` sets ``request._next_dep_cache`` to a dict."""
        backend = RegistryFormActionBackend()

        def handler() -> str:
            return "ok"

        backend.register_action(
            "only_handler",
            handler=handler,
            file_path=_FAKE_FILE,
            scope="shared",
        )

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

        backend.register_action(
            "dep_handler",
            handler=handler,
            file_path=_FAKE_FILE,
            scope="shared",
        )

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
        ``_resolve_form_class``, ``form.get_initial``, and the handler.
        The resolver's named-dependency cache keys on the ``Depends`` name,
        so any subsequent phase that asks for the same name hits the cache.
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
            cls, _ = _resolve_form_class(factory, request, {}, dep_cache, dep_stack)
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


class TestBuildFormNamespaceEdgeCases:
    """build_form_namespace_for_action edge cases."""

    def test_returns_none_when_no_backends_have_action(self, mock_http_request) -> None:
        """Returns None when no backend has meta for the given action."""
        req = mock_http_request(method="GET")
        result = build_form_namespace_for_action("nonexistent_xyz", req)
        assert result is None

    def test_returns_none_when_action_has_no_form_class(
        self, mock_http_request
    ) -> None:
        """Returns None for a form-less action (handler only, no form_class)."""
        req = mock_http_request(method="GET")
        result = build_form_namespace_for_action("test_no_form", req)
        assert result is None

    def test_returns_namespace_when_action_has_form_class(
        self, mock_http_request
    ) -> None:
        """Returns a SimpleNamespace with form when action has form_class."""
        req = mock_http_request(method="GET")
        result = build_form_namespace_for_action("simple_form", req)
        assert result is not None
        assert hasattr(result, "form")


@pytest.mark.django_db()
class TestBuildWizardNamespace:
    """build_form_namespace_for_action returns the wizard's form+wizard namespace."""

    def test_wizard_action_yields_form_and_wizard(self, mock_http_request) -> None:
        """A wizard action produces a namespace carrying the current step form."""
        req = mock_http_request(method="GET", resolver_match=None)
        result = build_form_namespace_for_action("dispatch_wizard", req)
        assert result is not None
        assert isinstance(result.form, WizardIdentityStep)
        assert isinstance(result.wizard, DispatchWizard)


@pytest.mark.django_db()
class TestWizardDispatchViaClient:
    """FormWizard step routing, finalisation, and re-render via the test client."""

    def _post_step(
        self,
        client,
        step: str,
        data: dict,
        *,
        action: str = "dispatch_wizard",
        origin: str = "/request/identity/",
    ):
        url = form_action_manager.get_action_url(action)
        payload = {
            "_url_param_step": step,
            "_next_form_origin": origin,
            "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            **data,
        }
        return client.post(url, data=payload, follow=False)

    def test_valid_first_step_redirects_to_next(self, client_no_csrf) -> None:
        """A valid first step advances to the next step via goto."""
        resp = self._post_step(client_no_csrf, "identity", {"name": "Ada"})
        assert resp.status_code == 302
        assert resp.url == "/request/scope/"

    def test_invalid_step_re_renders_page(self, client_no_csrf) -> None:
        """An invalid step re-renders the page and stores nothing."""
        resp = self._post_step(client_no_csrf, "identity", {"name": ""})
        assert resp.status_code == 200

    def test_last_step_calls_done_with_merged_data(self, client_no_csrf) -> None:
        """The final step finalises with the merged cleaned data and clears storage."""
        DispatchWizard.done_payloads.clear()
        self._post_step(client_no_csrf, "identity", {"name": "Ada"})
        resp = self._post_step(client_no_csrf, "scope", {"scope": "ops"})
        assert resp.status_code == 302
        assert resp.url == "/thanks/"
        assert DispatchWizard.done_payloads == [{"name": "Ada", "scope": "ops"}]

    def test_unknown_step_returns_bad_request(self, client_no_csrf) -> None:
        """A wizard whose current step has no form class returns 400."""

        class EmptyDispatchWizard(FormWizard):
            class Meta:
                steps: ClassVar = []

            def done(self, request, cleaned_data) -> HttpResponseRedirect:
                return HttpResponseRedirect("/thanks/")

        url = form_action_manager.get_action_url("empty_dispatch_wizard")
        resp = client_no_csrf.post(
            url,
            data={
                "_url_param_step": "ghost",
                "_next_form_origin": "/request/identity/",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        assert resp.status_code == 400

    def test_back_navigation_prefills_from_storage(
        self, client_no_csrf, mock_http_request
    ) -> None:
        """A revisited step rebuilds its form prefilled from saved data."""
        self._post_step(client_no_csrf, "identity", {"name": "Ada"})
        session_key = client_no_csrf.session.session_key
        store = SessionStore(session_key=session_key)
        req = mock_http_request(method="GET", session=store, resolver_match=None)
        namespace = build_form_namespace_for_action("dispatch_wizard", req)
        assert namespace.form.initial == {"name": "Ada"}

    def test_conditional_step_routing(self, client_no_csrf) -> None:
        """A steps_for override inserts a step the wizard then routes through."""
        ConditionalDispatchWizard.done_payloads.clear()
        first = self._post_step(
            client_no_csrf,
            "identity",
            {"name": "needs-extra"},
            action="conditional_dispatch_wizard",
        )
        assert first.status_code == 302
        assert first.url == "/request/extra/"

    def test_get_form_kwargs_feeds_step_form(self, client_no_csrf) -> None:
        """A get_form_kwargs override supplies the prefix the bound form expects."""
        KwargsDispatchWizard.seen_kwargs.clear()
        resp = self._post_step(
            client_no_csrf,
            "identity",
            {"wiz-name": "Ada"},
            action="kwargs_dispatch_wizard",
        )
        assert resp.status_code == 302
        assert resp.url == "/thanks/"
        assert KwargsDispatchWizard.seen_kwargs[0][0] == "identity"
