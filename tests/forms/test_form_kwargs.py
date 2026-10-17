from collections.abc import Callable
from typing import Any, ClassVar

import pytest
from django import forms as django_forms
from django.contrib.auth.models import Group
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict

from next.deps import REQUEST_DEP_CACHE_ATTR, Depends
from next.forms import (
    ActionRegistration,
    Form,
    FormWizard,
    ModelForm,
    PermissionOutcome,
    RegistryFormActionBackend,
)
from next.forms.base import BaseForm, BaseModelForm
from next.forms.dispatch import FormActionDispatch
from next.forms.dispatch.build import _form_action_context_callable
from next.forms.manager import build_form_namespace_for_action, form_action_manager
from next.forms.uid import ORIGIN_FIELD_NAME
from next.testing import NextClient, envelope_of, override_dependency
from next.urls import DUrl
from tests.support import bound_dependency


_FAKE_FILE = "/fake/myapp/forms.py"
_ACTION = "kwargs_action"
_DENIED = HttpResponse("nope", status=403)


def _kwargs_form(result: object, calls: list[str]) -> type[Form]:
    """Build a form whose overridden hook returns `result` and logs every hook."""

    class KwargsForm(Form):
        name = django_forms.CharField(max_length=40)

        @classmethod
        def get_initial(cls) -> dict[str, Any]:
            calls.append("get_initial")
            return {"name": "from-initial"}

        @classmethod
        def get_form_kwargs(cls, request: HttpRequest) -> object:
            calls.append("get_form_kwargs")
            return result

    return KwargsForm


def _initial_only_form(calls: list[str]) -> type[Form]:
    """Build a form that only overrides get_initial."""

    class InitialOnlyForm(Form):
        name = django_forms.CharField(max_length=40)

        @classmethod
        def get_initial(cls) -> dict[str, Any]:
            calls.append("get_initial")
            return {"name": "from-initial"}

    return InitialOnlyForm


def _post(data: dict[str, str] | None = None, *, origin: str | None = None):
    post = QueryDict(mutable=True)
    for key, value in (data or {}).items():
        post[key] = value
    if origin is not None:
        post[ORIGIN_FIELD_NAME] = origin
    return post


def _dispatch(
    mock_http_request,
    form_class: object,
    data: dict[str, str] | None = None,
    *,
    origin: str | None = None,
    handler: Callable[..., object] | None = None,
) -> tuple[HttpResponse, django_forms.Form | None]:
    """POST through the dispatch pipeline and return the response and bound form."""
    captured: dict[str, django_forms.Form] = {}

    def capture(form: django_forms.Form) -> HttpResponseRedirect:
        captured["form"] = form
        return HttpResponseRedirect("/done/")

    backend = RegistryFormActionBackend()
    backend.register_action(
        ActionRegistration(
            name=_ACTION,
            file_path=_FAKE_FILE,
            scope="shared",
            handler=handler or capture,
            form_class=form_class,
        )
    )
    request = mock_http_request(
        method="POST", POST=_post(data, origin=origin), FILES=QueryDict()
    )
    meta = backend.get_meta(_ACTION)
    assert meta is not None
    response = FormActionDispatch.dispatch(backend, request, _ACTION, meta)
    return response, captured.get("form")


def _posted_form(mock_http_request, form_class: object) -> django_forms.Form:
    response, form = _dispatch(mock_http_request, form_class, {"name": "posted"})
    assert response.status_code == 302
    assert form is not None
    assert form.is_bound
    return form


def _rendered_form(mock_http_request, form_class: object) -> django_forms.Form:
    request = mock_http_request(method="GET")
    form = _form_action_context_callable(form_class)(request).form
    assert not form.is_bound
    return form


_BUILDERS = pytest.mark.parametrize(
    "build", [_posted_form, _rendered_form], ids=("post", "render")
)


class TestHookPresenceFlag:
    """`__init_subclass__` stamps `_has_get_form_kwargs` by `__func__` identity."""

    @pytest.mark.parametrize("base", [BaseForm, BaseModelForm, Form, ModelForm])
    def test_the_bases_carry_no_override(self, base: type) -> None:
        assert base._has_get_form_kwargs is False

    def test_a_form_without_the_override_stays_off(self) -> None:
        assert _initial_only_form([])._has_get_form_kwargs is False

    def test_an_override_turns_the_flag_on(self) -> None:
        assert _kwargs_form({}, [])._has_get_form_kwargs is True

    def test_a_model_form_override_turns_the_flag_on(self) -> None:
        class GroupKwargsForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]

            @classmethod
            def get_form_kwargs(cls, request: HttpRequest) -> dict[str, Any]:
                return {}

        assert GroupKwargsForm._has_get_form_kwargs is True

    def test_a_subclass_inherits_the_override(self) -> None:
        parent = _kwargs_form({}, [])

        class ChildForm(parent):
            extra = django_forms.CharField(required=False)

        assert ChildForm._has_get_form_kwargs is True


class TestDefaultGetFormKwargs:
    """The inherited hook maps `get_initial` to `initial` or `instance`."""

    def test_a_form_gets_its_initial(self, mock_http_request) -> None:
        form_class = _initial_only_form([])
        request = mock_http_request(method="GET")
        assert form_class.get_form_kwargs(request) == {
            "initial": {"name": "from-initial"}
        }

    def test_get_initial_reads_the_url_kwargs_of_the_origin(
        self, mock_http_request
    ) -> None:
        class SlugForm(Form):
            name = django_forms.CharField()

            @classmethod
            def get_initial(cls, name: DUrl[str]) -> dict[str, Any]:
                return {"name": name}

        request = mock_http_request(
            method="POST", POST=_post(origin="/groups/editors/")
        )
        assert SlugForm.get_form_kwargs(request) == {"initial": {"name": "editors"}}

    def test_get_initial_shares_the_dispatch_dependency_cache(
        self, mock_http_request
    ) -> None:
        class TenantForm(Form):
            name = django_forms.CharField()

            @classmethod
            def get_initial(cls, tenant: str = Depends("tenant")) -> dict[str, Any]:
                return {"name": tenant}

        request = mock_http_request(method="GET")
        setattr(request, REQUEST_DEP_CACHE_ATTR, {"tenant": "cached"})
        with override_dependency("tenant", "fresh"):
            kwargs = TenantForm.get_form_kwargs(request)
        assert kwargs == {"initial": {"name": "cached"}}

    @pytest.mark.django_db()
    def test_a_model_form_gets_the_url_instance(self, mock_http_request) -> None:
        group = Group.objects.create(name="editors")

        class GroupByNameForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name"

        request = mock_http_request(
            method="POST", POST=_post(origin="/groups/editors/")
        )
        assert GroupByNameForm.get_form_kwargs(request) == {"instance": group}

    def test_a_model_instance_on_a_plain_form_is_refused(
        self, mock_http_request
    ) -> None:
        class InstanceForm(Form):
            name = django_forms.CharField()

            @classmethod
            def get_initial(cls) -> object:
                return Group(name="editors")

        with pytest.raises(TypeError, match=r"InstanceForm is not a ModelForm"):
            InstanceForm.get_form_kwargs(mock_http_request(method="GET"))


class TestOverrideOnPost:
    """The dispatcher builds the bound form from the overridden hook alone."""

    def test_the_result_feeds_the_constructor_whole(self, mock_http_request) -> None:
        calls: list[str] = []
        form_class = _kwargs_form(
            {"prefix": "p", "initial": {"name": "seed"}, "label_suffix": "!"}, calls
        )
        response, form = _dispatch(mock_http_request, form_class, {"p-name": "typed"})
        assert response.status_code == 302
        assert form is not None
        assert form.prefix == "p"
        assert form.label_suffix == "!"
        assert form.initial == {"name": "seed"}
        assert form.cleaned_data == {"name": "typed"}
        assert calls == ["get_form_kwargs"]

    def test_an_empty_result_is_used_as_is(self, mock_http_request) -> None:
        calls: list[str] = []
        response, form = _dispatch(
            mock_http_request, _kwargs_form({}, calls), {"name": "typed"}
        )
        assert response.status_code == 302
        assert form is not None
        assert form.initial == {}
        assert form.prefix is None
        assert calls == ["get_form_kwargs"]

    def test_the_hook_parameters_are_dependency_injected(
        self, mock_http_request
    ) -> None:
        seen: dict[str, object] = {}

        class InjectedForm(Form):
            name = django_forms.CharField()

            @classmethod
            def get_form_kwargs(
                cls,
                request: HttpRequest,
                name: DUrl[str],
                tenant: str = Depends("tenant"),
            ) -> dict[str, Any]:
                seen["request"] = request
                return {"initial": {"name": f"{tenant}-{name}"}}

        with override_dependency("tenant", "acme"):
            response, form = _dispatch(
                mock_http_request,
                InjectedForm,
                {"name": "typed"},
                origin="/groups/editors/",
            )
        assert response.status_code == 302
        assert form is not None
        assert form.initial == {"name": "acme-editors"}
        assert isinstance(seen["request"], HttpRequest)

    def test_a_view_denial_never_reaches_the_hook(self, mock_http_request) -> None:
        calls: list[str] = []

        class DeniedForm(Form):
            name = django_forms.CharField()

            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                calls.append("check_permissions")
                return _DENIED

            @classmethod
            def get_form_kwargs(cls, request: HttpRequest) -> dict[str, Any]:
                calls.append("get_form_kwargs")
                return {}

        response, form = _dispatch(mock_http_request, DeniedForm, {"name": "x"})
        assert response is _DENIED
        assert form is None
        assert calls == ["check_permissions"]

    def test_the_hook_runs_between_the_view_and_object_gates(
        self, mock_http_request
    ) -> None:
        calls: list[str] = []

        class GatedForm(Form):
            name = django_forms.CharField()

            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                calls.append("check_permissions")
                return None

            @classmethod
            def get_form_kwargs(cls, request: HttpRequest) -> dict[str, Any]:
                calls.append("get_form_kwargs")
                return {"prefix": "g"}

            def has_object_permission(self) -> PermissionOutcome:
                calls.append(f"has_object_permission:{self.prefix}")
                return None

        response, _form = _dispatch(mock_http_request, GatedForm, {"g-name": "x"})
        assert response.status_code == 302
        assert calls == [
            "check_permissions",
            "get_form_kwargs",
            "has_object_permission:g",
        ]

    @pytest.mark.django_db()
    def test_super_on_a_model_form_keeps_the_url_instance(
        self, mock_http_request
    ) -> None:
        group = Group.objects.create(name="editors")

        class PrefixedGroupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name"

            @classmethod
            def get_form_kwargs(cls, request: HttpRequest) -> dict[str, Any]:
                return {**super().get_form_kwargs(request), "prefix": "g"}

        def save(form: django_forms.ModelForm) -> HttpResponseRedirect:
            form.save()
            return HttpResponseRedirect("/done/")

        response, _form = _dispatch(
            mock_http_request,
            PrefixedGroupForm,
            {"g-name": "renamed"},
            origin="/groups/editors/",
            handler=save,
        )
        assert response.status_code == 302
        assert list(Group.objects.values_list("pk", "name")) == [(group.pk, "renamed")]

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            pytest.param(
                [("prefix", "p")],
                r"^Action 'kwargs_action': KwargsForm\.get_form_kwargs returned "
                r"list, expected a dict",
                id="list",
            ),
            pytest.param(
                None,
                r"^Action 'kwargs_action': KwargsForm\.get_form_kwargs returned "
                r"NoneType, expected a dict",
                id="none",
            ),
            pytest.param(
                {"data": {}},
                r"^Action 'kwargs_action': KwargsForm\.get_form_kwargs returned "
                r"the 'data' key",
                id="data",
            ),
            pytest.param(
                {"prefix": "p", "files": {}},
                r"^Action 'kwargs_action': KwargsForm\.get_form_kwargs returned "
                r"the 'files' key",
                id="files",
            ),
        ],
    )
    def test_a_malformed_result_names_the_action(
        self, mock_http_request, result: object, expected: str
    ) -> None:
        form_class = _kwargs_form(result, [])
        with pytest.raises(TypeError, match=expected):
            _dispatch(mock_http_request, form_class, {"name": "x"})


@pytest.mark.django_db()
class TestOverrideOnValidateProbe:
    """A validate-only probe checks the form the hook built and runs no handler."""

    @pytest.mark.parametrize(
        ("value", "valid", "errors"),
        [("abc", True, []), ("too long", False, ["name"])],
        ids=("valid", "invalid"),
    )
    def test_the_probe_validates_the_hook_built_form(
        self, value: str, valid, errors: list[str]
    ) -> None:
        calls: list[str] = []

        class ProbedKwargsForm(Form):
            name = django_forms.CharField(max_length=3)

            @classmethod
            def get_form_kwargs(cls, request: HttpRequest) -> dict[str, Any]:
                calls.append("get_form_kwargs")
                return {"prefix": "p"}

            def on_valid(self, request: HttpRequest) -> None:
                calls.append("on_valid")

        response = NextClient(enforce_csrf_checks=False).post_action(
            "probed_kwargs_form",
            {"p-name": value},
            origin="/",
            partial=True,
            headers={"X-Next-Validate": "name"},
        )
        assert response.status_code == 200
        meta = envelope_of(response).form_meta()
        assert meta is not None
        assert meta["valid"] is valid
        assert list(meta["errors"]) == errors
        assert calls == ["get_form_kwargs"]


class TestOverrideOnRender:
    """The page render builds the unbound form from the overridden hook alone."""

    def test_the_result_feeds_the_unbound_form(self, mock_http_request) -> None:
        calls: list[str] = []
        form_class = _kwargs_form({"prefix": "r", "initial": {"name": "seed"}}, calls)
        form = _rendered_form(mock_http_request, form_class)
        assert form.prefix == "r"
        assert form.initial == {"name": "seed"}
        assert calls == ["get_form_kwargs"]

    def test_super_on_a_form_keeps_the_initial(self, mock_http_request) -> None:
        class SuffixedForm(Form):
            name = django_forms.CharField()

            @classmethod
            def get_initial(cls) -> dict[str, Any]:
                return {"name": "seed"}

            @classmethod
            def get_form_kwargs(cls, request: HttpRequest) -> dict[str, Any]:
                return {**super().get_form_kwargs(request), "label_suffix": "?"}

        form = _rendered_form(mock_http_request, SuffixedForm)
        assert form.initial == {"name": "seed"}
        assert form.label_suffix == "?"

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            pytest.param(
                "prefix", r"^KwargsForm\.get_form_kwargs returned str", id="str"
            ),
            pytest.param(
                {"data": {}},
                r"^KwargsForm\.get_form_kwargs returned the 'data' key",
                id="data",
            ),
        ],
    )
    def test_a_malformed_result_raises_without_an_action(
        self, mock_http_request, result: object, expected: str
    ) -> None:
        with pytest.raises(TypeError, match=expected):
            _rendered_form(mock_http_request, _kwargs_form(result, []))


class TestFactoryCompatibility:
    """A form factory keeps its contract next to the kwargs hook."""

    @_BUILDERS
    def test_a_non_empty_pair_bypasses_both_hooks(
        self, mock_http_request, build
    ) -> None:
        calls: list[str] = []
        form_class = _kwargs_form({"prefix": "hook"}, calls)

        def factory() -> tuple[type[Form], dict[str, Any]]:
            return form_class, {"label_suffix": "#"}

        form = build(mock_http_request, factory)
        assert form.label_suffix == "#"
        assert form.prefix is None
        assert calls == []

    @_BUILDERS
    def test_an_empty_pair_falls_back_to_the_hook(
        self, mock_http_request, build
    ) -> None:
        calls: list[str] = []
        form_class = _kwargs_form({}, calls)

        def factory() -> tuple[type[Form], dict[str, Any]]:
            return form_class, {}

        build(mock_http_request, factory)
        assert calls == ["get_form_kwargs"]

    @_BUILDERS
    def test_a_produced_class_with_the_override_runs_it(
        self, mock_http_request, build
    ) -> None:
        calls: list[str] = []
        form_class = _kwargs_form({"label_suffix": "%"}, calls)

        def factory() -> type[Form]:
            return form_class

        form = build(mock_http_request, factory)
        assert form.label_suffix == "%"
        assert calls == ["get_form_kwargs"]

    @_BUILDERS
    def test_a_produced_class_without_the_override_reads_get_initial(
        self, mock_http_request, build
    ) -> None:
        calls: list[str] = []
        form_class = _initial_only_form(calls)

        def factory() -> type[Form]:
            return form_class

        form = build(mock_http_request, factory)
        assert form.initial == {"name": "from-initial"}
        assert calls == ["get_initial"]


class TestNoOverrideFastPath:
    """Without an override the dispatcher never asks the kwargs hook."""

    @_BUILDERS
    def test_the_hook_is_never_called(
        self, mock_http_request, monkeypatch, build
    ) -> None:
        calls: list[str] = []
        form_class = _initial_only_form(calls)

        def refuse(cls: type, request: HttpRequest) -> dict[str, Any]:
            pytest.fail("get_form_kwargs ran without an override")

        monkeypatch.setattr(form_class, "get_form_kwargs", classmethod(refuse))
        form = build(mock_http_request, form_class)
        assert form.initial == {"name": "from-initial"}
        assert calls == ["get_initial"]


class KwargsHookStep(Form):
    """Wizard step whose own kwargs hook must stay out of the wizard flow."""

    name = django_forms.CharField(max_length=40)
    hook_calls: ClassVar[list[str]] = []

    @classmethod
    def get_form_kwargs(cls, request: HttpRequest) -> dict[str, Any]:
        cls.hook_calls.append("get_form_kwargs")
        return {"prefix": "never"}


class KwargsHookWizard(FormWizard):
    """One-step wizard over a step form that overrides the kwargs hook."""

    class Meta:
        """A single step whose form declares get_form_kwargs."""

        steps: ClassVar = [("identity", KwargsHookStep)]

    done_payloads: ClassVar[list[dict[str, Any]]] = []

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Record the cleaned data and redirect."""
        type(self).done_payloads.append(cleaned_data)
        return HttpResponseRedirect("/thanks/")


@pytest.mark.django_db()
class TestWizardStepsIgnoreTheFormHook:
    """A wizard builds its step forms through its own `get_form_kwargs` only."""

    @pytest.fixture(autouse=True)
    def _clear_logs(self) -> None:
        KwargsHookStep.hook_calls.clear()
        KwargsHookWizard.done_payloads.clear()

    def test_a_step_post_finishes_without_the_form_hook(self, client_no_csrf) -> None:
        url = form_action_manager.get_action_url("kwargs_hook_wizard")
        response = client_no_csrf.post(
            url, data={ORIGIN_FIELD_NAME: "/request/identity/", "name": "Ada"}
        )
        assert response.status_code == 302
        assert response.url == "/thanks/"
        assert KwargsHookWizard.done_payloads == [{"name": "Ada"}]
        assert KwargsHookStep.hook_calls == []

    def test_the_step_render_skips_the_form_hook(self, mock_http_request) -> None:
        request = mock_http_request(method="GET", resolver_match=None)
        namespace = build_form_namespace_for_action("kwargs_hook_wizard", request)
        assert namespace is not None
        assert isinstance(namespace.form, KwargsHookStep)
        assert namespace.form.prefix is None
        assert KwargsHookStep.hook_calls == []


def _shared_tenant_form() -> type[Form]:
    """Build a form whose override and get_initial both take the tenant dependency."""

    class SharedTenantForm(Form):
        name = django_forms.CharField()

        @classmethod
        def get_initial(cls, tenant: str = Depends("tenant")) -> dict[str, Any]:
            return {"name": tenant}

        @classmethod
        def get_form_kwargs(
            cls, request: HttpRequest, tenant: str = Depends("tenant")
        ) -> dict[str, Any]:
            return {**super().get_form_kwargs(request), "prefix": tenant}

    return SharedTenantForm


class TestCallerCacheOnTheRequest:
    """`super().get_form_kwargs` resolves in the dependency cache of its caller."""

    def test_a_render_resolves_the_shared_dependency_once(
        self, mock_http_request
    ) -> None:
        calls: list[str] = []

        def tenant() -> str:
            calls.append("tenant")
            return "acme"

        request = mock_http_request(method="GET")
        with bound_dependency("tenant", tenant):
            form = _form_action_context_callable(_shared_tenant_form())(request).form
        assert calls == ["tenant"]
        assert form.prefix == "acme"
        assert form.initial == {"name": "acme"}
        assert not hasattr(request, REQUEST_DEP_CACHE_ATTR)

    def test_a_render_hands_back_the_cache_the_request_carried(
        self, mock_http_request
    ) -> None:
        def tenant() -> str:
            return "acme"

        carried: dict[str, object] = {}
        request = mock_http_request(method="GET")
        setattr(request, REQUEST_DEP_CACHE_ATTR, carried)
        build = _form_action_context_callable(_shared_tenant_form())
        with bound_dependency("tenant", tenant):
            form = build(request, {}).form
        assert form.prefix == "acme"
        assert getattr(request, REQUEST_DEP_CACHE_ATTR) is carried

    def test_a_post_resolves_the_shared_dependency_once(
        self, mock_http_request
    ) -> None:
        calls: list[str] = []
        seen: dict[str, object] = {}

        def tenant() -> str:
            calls.append("tenant")
            return "acme"

        def keep(request: HttpRequest, form: django_forms.Form) -> HttpResponseRedirect:
            seen["request"] = request
            seen["cache"] = getattr(request, REQUEST_DEP_CACHE_ATTR)
            seen["prefix"] = form.prefix
            return HttpResponseRedirect("/done/")

        with bound_dependency("tenant", tenant):
            response, _form = _dispatch(
                mock_http_request,
                _shared_tenant_form(),
                {"acme-name": "posted"},
                handler=keep,
            )
        assert response.status_code == 302
        assert calls == ["tenant"]
        assert seen["prefix"] == "acme"
        assert getattr(seen["request"], REQUEST_DEP_CACHE_ATTR) is seen["cache"]
