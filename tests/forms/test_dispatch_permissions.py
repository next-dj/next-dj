from pathlib import Path
from typing import Any, ClassVar

import pytest
from django import forms as django_forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import ActionGuard, Form, FormWizard, ModelForm, action
from next.forms.dispatch import FormActionDispatch, _branch
from next.forms.dispatch.permissions import (
    FORM_OBJECT,
    _check_access,
    _object_permissions,
    guard_rule,
)
from next.forms.manager import form_action_manager
from next.forms.signals import form_access_denied
from next.forms.uid import ORIGIN_FIELD_NAME
from next.permissions import add_perm
from next.permissions.markers import OPEN, AccessRule, Requirement, predicate
from next.testing import SignalRecorder, capture_signals
from tests.support import owns_group


User = get_user_model()

pytestmark = [
    pytest.mark.django_db(),
    pytest.mark.usefixtures("isolated_permissions", "object_permission_backend"),
]

_EDIT = "auth.edit_group"
_MODEL_PERM = "auth.change_group"
_FORMS = "/x/forms.py"


class _Rule:
    """A registered predicate that answers by a switch and logs every object it gets."""

    def __init__(self, log: list[tuple[str, object]]) -> None:
        self.log = log
        self.allow = lambda: True

    def __call__(self, user: object, obj: object) -> bool:
        self.log.append(("predicate", obj))
        return predicate(self.allow)(user, obj)

    def objects(self) -> list[object]:
        return [obj for step, obj in self.log if step == "predicate"]


@pytest.fixture()
def log() -> list[tuple[str, object]]:
    return []


@pytest.fixture()
def rule(isolated_permissions, log) -> _Rule:
    rule = _Rule(log)
    add_perm(_EDIT, rule)
    return rule


@pytest.fixture()
def denials() -> SignalRecorder:
    with capture_signals(form_access_denied) as recorder:
        yield recorder


def _member(client, username: str, *codenames: str):
    user = User.objects.create_user(username)
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    client.force_login(user)
    return user


def _post(client, action_name: str, origin: str = "/", **data: str) -> HttpResponse:
    url = form_action_manager.get_action_url(action_name)
    return client.post(url, data={ORIGIN_FIELD_NAME: origin, **data})


def _rule(guard: ActionGuard, *, form: bool) -> AccessRule:
    branch = {"form_class": _PermStep} if form else {"handler": _handler}
    return guard_rule({"guard": guard, "file_path": _FORMS, **branch})


def _guarded_request(rf, user: object) -> HttpRequest:
    request = rf.post("/_next/form/x/")
    request.user = user
    return request


def _post_owned(client, name: str = "ada") -> HttpResponse:
    return _post(client, "perm_owned_group_form", "/groups/ada/", name=name)


def _layers(recorder: SignalRecorder) -> list[tuple[str, str]]:
    return [
        (event.kwargs["layer"], event.kwargs["reason"])
        for event in recorder.events_for(form_access_denied)
    ]


@pytest.fixture()
def owned_group_form(log) -> type:
    class PermOwnedGroupForm(ModelForm):
        class Meta:
            model = Group
            fields: ClassVar[list[str]] = ["name"]
            instance_from_url = "name"
            permission_required = _EDIT

        def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
            self.save()
            log.append(("saved", self.instance.name))
            return HttpResponseRedirect("/done/")

    return PermOwnedGroupForm


class TestSavedInstance:
    """The predicate answers for the saved row the model form edits."""

    @pytest.fixture(autouse=True)
    def _owner_rule(self, rule: _Rule, owned_group_form: type) -> None:
        rule.allow = owns_group

    def test_the_predicate_receives_the_bound_instance(
        self, client_no_csrf, rule: _Rule
    ) -> None:
        group = Group.objects.create(name="ada")
        _member(client_no_csrf, "ada")
        _post_owned(client_no_csrf)
        (obj,) = rule.objects()
        assert isinstance(obj, Group)
        assert obj.pk == group.pk

    def test_the_owner_passes_and_the_row_is_saved(
        self, client_no_csrf, log, denials
    ) -> None:
        Group.objects.create(name="ada")
        _member(client_no_csrf, "ada")
        response = _post_owned(client_no_csrf)
        assert response.status_code == 302
        assert response["Location"] == "/done/"
        assert ("saved", "ada") in log
        assert _layers(denials) == []

    def test_one_post_reads_the_guard_rule_once(
        self, client_no_csrf, log, monkeypatch
    ) -> None:
        metas: list[object] = []

        def counted(meta):
            metas.append(meta)
            return guard_rule(meta)

        monkeypatch.setattr("next.forms.dispatch.guard_rule", counted)
        Group.objects.create(name="ada")
        _member(client_no_csrf, "ada")
        _post_owned(client_no_csrf)
        assert ("saved", "ada") in log
        assert len(metas) == 1

    def test_another_user_gets_403_and_nothing_is_saved(
        self, client_no_csrf, log, denials
    ) -> None:
        group = Group.objects.create(name="ada")
        _member(client_no_csrf, "bob")
        response = _post_owned(client_no_csrf, "renamed")
        assert response.status_code == 403
        assert _layers(denials) == [("object", "denied")]
        event = denials.first_for(form_access_denied)
        assert event.kwargs["action_name"] == "perm_owned_group_form"
        group.refresh_from_db()
        assert group.name == "ada"
        assert not Group.objects.filter(name="renamed").exists()
        assert [step for step, _value in log] == ["predicate"]

    def test_a_superuser_never_reaches_the_predicate(
        self, client_no_csrf, rule: _Rule
    ) -> None:
        Group.objects.create(name="ada")
        root = User.objects.create_superuser("root", "root@example.com")
        client_no_csrf.force_login(root)
        response = _post_owned(client_no_csrf)
        assert response.status_code == 302
        assert rule.objects() == []


class TestNoObject:
    """A new record or a plain form leaves the predicate no object."""

    def test_a_new_record_gives_none(self, client_no_csrf, rule: _Rule) -> None:
        class PermNewGroupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                permission_required = _EDIT

            def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
                self.save()
                return HttpResponseRedirect("/done/")

        _member(client_no_csrf, "ada")
        response = _post(client_no_csrf, "perm_new_group_form", name="fresh")
        assert response.status_code == 302
        assert rule.objects() == [None]
        assert Group.objects.filter(name="fresh").exists()

    def test_a_new_record_is_refused_by_a_denying_predicate(
        self, client_no_csrf, rule: _Rule, denials
    ) -> None:
        rule.allow = lambda: False

        class PermRefusedGroupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                permission_required = _EDIT

        _member(client_no_csrf, "ada")
        response = _post(client_no_csrf, "perm_refused_group_form", name="fresh")
        assert response.status_code == 403
        assert _layers(denials) == [("object", "denied")]
        assert not Group.objects.filter(name="fresh").exists()

    def test_a_plain_form_gives_none(self, client_no_csrf, rule: _Rule) -> None:
        class PermPlainForm(Form):
            name = django_forms.CharField(max_length=20)

            class Meta:
                permission_required = _EDIT

            def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
                return HttpResponseRedirect("/done/")

        _member(client_no_csrf, "ada")
        response = _post(client_no_csrf, "perm_plain_form", name="x")
        assert response.status_code == 302
        assert rule.objects() == [None]


class TestObjectHook:
    """The registered check runs first and an overridden hook still runs after it."""

    @pytest.fixture()
    def hooked_form(self, log) -> type:
        class PermHookedGroupForm(ModelForm):
            hook_allows: ClassVar[bool] = True

            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name"
                permission_required = _EDIT

            def has_object_permission(self, request: HttpRequest) -> bool:
                log.append(("hook", self.instance))
                return type(self).hook_allows

            def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
                return HttpResponseRedirect("/done/")

        return PermHookedGroupForm

    def _submit(self, client) -> HttpResponse:
        Group.objects.create(name="ada")
        _member(client, "ada")
        return _post(client, "perm_hooked_group_form", "/groups/ada/", name="ada")

    def test_a_denying_predicate_denies_though_the_hook_allows(
        self, client_no_csrf, rule: _Rule, hooked_form, log, denials
    ) -> None:
        rule.allow = lambda: False
        assert self._submit(client_no_csrf).status_code == 403
        assert [step for step, _obj in log] == ["predicate"]
        assert _layers(denials) == [("object", "denied")]

    def test_a_denying_hook_denies_after_a_passing_predicate(
        self, client_no_csrf, rule: _Rule, hooked_form, log, denials
    ) -> None:
        hooked_form.hook_allows = False
        assert self._submit(client_no_csrf).status_code == 403
        assert [step for step, _obj in log] == ["predicate", "hook"]
        assert _layers(denials) == [("object", "denied")]

    def test_both_passing_dispatch(
        self, client_no_csrf, rule: _Rule, hooked_form, log
    ) -> None:
        response = self._submit(client_no_csrf)
        assert response.status_code == 302
        predicate_obj, hook_obj = (obj for _step, obj in log)
        assert predicate_obj is hook_obj


class _PermStep(Form):
    """A wizard step that records every construction."""

    name = django_forms.CharField(max_length=20)
    built: ClassVar[list[bool]] = []

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs) -> None:
        type(self).built.append(True)
        super().__init__(*args, **kwargs)


class TestGuardStageOnly:
    """A wizard or a handler-only action checks the registered perm up front."""

    @pytest.fixture(autouse=True)
    def _fresh_steps(self) -> None:
        _PermStep.built.clear()

    @pytest.fixture()
    def wizard(self) -> type:
        class PermWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("identity", _PermStep)]
                permission_required = _EDIT

            def done(
                self, request: HttpRequest, cleaned_data: dict[str, Any]
            ) -> HttpResponseRedirect:
                return HttpResponseRedirect("/thanks/")

        return PermWizard

    @pytest.fixture()
    def handler_calls(self) -> list[HttpRequest]:
        calls: list[HttpRequest] = []

        @action("perm_handler_action", permission_required=_EDIT)
        def perm_handler(request: HttpRequest) -> HttpResponse:
            calls.append(request)
            return HttpResponse("handled")

        return calls

    @pytest.mark.usefixtures("wizard")
    def test_a_wizard_passes_with_none_for_the_object(
        self, client_no_csrf, rule: _Rule
    ) -> None:
        _member(client_no_csrf, "ada")
        response = _post(client_no_csrf, "perm_wizard", "/request/identity/", name="x")
        assert response.status_code == 302
        assert response["Location"] == "/thanks/"
        assert rule.objects() == [None]

    @pytest.mark.usefixtures("wizard")
    def test_a_wizard_is_refused_before_a_step_binds(
        self, client_no_csrf, rule: _Rule, denials
    ) -> None:
        rule.allow = lambda: False
        _member(client_no_csrf, "ada")
        response = _post(client_no_csrf, "perm_wizard", "/request/identity/", name="x")
        assert response.status_code == 403
        assert rule.objects() == [None]
        assert _PermStep.built == []
        assert _layers(denials) == []

    def test_a_handler_runs_with_none_for_the_object(
        self, client_no_csrf, rule: _Rule, handler_calls
    ) -> None:
        _member(client_no_csrf, "ada")
        response = _post(client_no_csrf, "perm_handler_action")
        assert response.status_code == 200
        assert response.content == b"handled"
        assert rule.objects() == [None]
        assert len(handler_calls) == 1

    def test_a_handler_is_refused_before_it_runs(
        self, client_no_csrf, rule: _Rule, handler_calls, denials
    ) -> None:
        rule.allow = lambda: False
        _member(client_no_csrf, "ada")
        assert _post(client_no_csrf, "perm_handler_action").status_code == 403
        assert rule.objects() == [None]
        assert handler_calls == []
        assert _layers(denials) == []


class TestMixedGuard:
    """A model permission beside a registered one is still checked up front."""

    @pytest.fixture(autouse=True)
    def _mixed_form(self, rule: _Rule) -> None:
        rule.allow = owns_group

        class PermMixedGroupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name"
                permission_required = (_MODEL_PERM, _EDIT)

            def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
                return HttpResponseRedirect("/done/")

        Group.objects.create(name="ada")

    def _submit(self, client) -> HttpResponse:
        return _post(client, "perm_mixed_group_form", "/groups/ada/", name="ada")

    def test_without_the_model_perm_the_guard_refuses_first(
        self, client_no_csrf, rule: _Rule, denials
    ) -> None:
        _member(client_no_csrf, "ada")
        assert self._submit(client_no_csrf).status_code == 403
        assert rule.objects() == []
        assert _layers(denials) == []

    def test_with_the_model_perm_the_predicate_decides(
        self, client_no_csrf, rule: _Rule
    ) -> None:
        _member(client_no_csrf, "ada", "change_group")
        assert self._submit(client_no_csrf).status_code == 302
        (obj,) = rule.objects()
        assert obj.name == "ada"

    def test_with_the_model_perm_a_non_owner_is_refused_per_object(
        self, client_no_csrf, rule: _Rule, denials
    ) -> None:
        _member(client_no_csrf, "bob", "change_group")
        assert self._submit(client_no_csrf).status_code == 403
        assert len(rule.objects()) == 1
        assert _layers(denials) == [("object", "denied")]


class _GuardUser:
    """An authenticated user recording the exact sequence `has_perms` receives."""

    is_authenticated = True

    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.received: list[object] = []

    def has_perms(self, perms: object) -> bool:
        self.received.append(perms)
        return self.allowed


class TestNothingRegistered:
    """With no permission registered the guard behaves as it always did."""

    @pytest.fixture()
    def model_form(self) -> None:
        class PermModelOnlyForm(Form):
            name = django_forms.CharField(max_length=20)

            class Meta:
                permission_required = _MODEL_PERM

            def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
                return HttpResponseRedirect("/done/")

    @pytest.fixture()
    def has_perm_calls(self, monkeypatch) -> list[tuple[str, object]]:
        calls: list[tuple[str, object]] = []
        original = User.has_perm

        def spy(user, perm, obj=None):
            calls.append((perm, obj))
            return original(user, perm, obj)

        monkeypatch.setattr(User, "has_perm", spy)
        return calls

    @pytest.mark.parametrize("object_stage", [True, False])
    def test_has_perms_gets_the_guards_own_tuple(self, rf, object_stage) -> None:
        guard = ActionGuard(permissions=(_MODEL_PERM, "auth.add_group"))
        request = _guarded_request(rf, _GuardUser())
        assert _check_access(request, _rule(guard, form=object_stage)) is None
        (received,) = request.user.received
        assert received is guard.permissions

    def test_a_login_only_guard_never_asks_has_perms(self, rf) -> None:
        request = _guarded_request(rf, _GuardUser())
        guard = ActionGuard(login_required=True)
        assert _check_access(request, _rule(guard, form=True)) is None
        assert request.user.received == []

    @pytest.mark.usefixtures("model_form")
    def test_a_holder_of_the_model_perm_passes(
        self, client_no_csrf, has_perm_calls
    ) -> None:
        _member(client_no_csrf, "ada", "change_group")
        response = _post(client_no_csrf, "perm_model_only_form", name="x")
        assert response.status_code == 302
        assert has_perm_calls == [(_MODEL_PERM, None)]

    @pytest.mark.usefixtures("model_form")
    def test_a_user_without_it_is_refused_at_the_guard(
        self, client_no_csrf, has_perm_calls, denials
    ) -> None:
        _member(client_no_csrf, "ada")
        response = _post(client_no_csrf, "perm_model_only_form", name="x")
        assert response.status_code == 403
        assert has_perm_calls == [(_MODEL_PERM, None)]
        assert _layers(denials) == []


class TestGuardRuleMemo:
    """The rule of a guard is kept until the permission registry changes."""

    def test_reused_on_the_same_registry_version(self) -> None:
        guard = ActionGuard(permissions=(_MODEL_PERM, _EDIT))
        first = _rule(guard, form=True)
        assert _rule(guard, form=True) is first
        assert first.requirements[1].perms is guard.permissions
        assert _object_permissions(first) == ()

    def test_rebuilt_after_a_registration(self) -> None:
        guard = ActionGuard(permissions=(_MODEL_PERM, _EDIT))
        before = _rule(guard, form=True)
        add_perm(_EDIT, owns_group)
        after = _rule(guard, form=True)
        assert after is not before
        assert after.requirements == (
            Requirement("login", Path(_FORMS)),
            Requirement("permission", Path(_FORMS), (_MODEL_PERM,)),
            Requirement("permission", Path(_FORMS), (_EDIT,), FORM_OBJECT),
        )
        assert _rule(guard, form=True) is after

    def test_no_guard_asks_nothing(self) -> None:
        assert guard_rule({"guard": None, "file_path": _FORMS}) is OPEN

    @pytest.mark.parametrize(
        ("guard", "form"),
        [
            pytest.param(ActionGuard(login_required=True), True, id="no-permissions"),
            pytest.param(
                ActionGuard(permissions=(_MODEL_PERM,)), True, id="none-registered"
            ),
            pytest.param(ActionGuard(permissions=(_EDIT,)), False, id="not-a-form"),
        ],
    )
    def test_no_object_permissions(self, guard: ActionGuard, form) -> None:
        add_perm(_EDIT, owns_group)
        assert _object_permissions(_rule(guard, form=form)) == ()

    def test_object_permissions_keep_the_guard_order(self) -> None:
        add_perm("auth.b_group", owns_group)
        add_perm("auth.a_group", owns_group)
        guard = ActionGuard(permissions=("auth.a_group", _MODEL_PERM, "auth.b_group"))
        rule = _rule(guard, form=True)
        assert _object_permissions(rule) == ("auth.a_group", "auth.b_group")

    def test_a_registered_only_guard_leaves_nothing_up_front(self, rf) -> None:
        add_perm(_EDIT, owns_group)
        request = _guarded_request(rf, _GuardUser(allowed=False))
        guard = ActionGuard(permissions=(_EDIT,))
        assert _check_access(request, _rule(guard, form=True)) is None
        assert request.user.received == []
        with pytest.raises(PermissionDenied):
            _check_access(request, _rule(guard, form=False))


def _handler(request: HttpRequest) -> HttpResponse:
    return HttpResponse("handled")


class _BranchWizard(FormWizard):
    """A wizard the branch table names without dispatching it."""

    class Meta:
        abstract = True
        steps: ClassVar = [("identity", _PermStep)]


class TestBranch:
    """`_branch` names the route an action takes, which the guard reads too."""

    @pytest.mark.parametrize(
        ("meta", "expected"),
        [
            pytest.param({"wizard_class": _BranchWizard}, "wizard", id="wizard"),
            pytest.param(
                {"wizard_class": _BranchWizard, "form_class": _PermStep},
                "wizard",
                id="wizard-wins",
            ),
            pytest.param({"form_class": _PermStep}, "form", id="form"),
            pytest.param(
                {"form_class": _PermStep, "handler": _handler},
                "form",
                id="form-with-handler",
            ),
            pytest.param({"handler": _handler}, "handler", id="handler"),
            pytest.param({}, None, id="none"),
        ],
    )
    def test_branch(self, meta, expected) -> None:
        assert _branch(meta) == expected

    @pytest.mark.parametrize(
        ("meta", "deferred"),
        [
            pytest.param({"form_class": _PermStep}, True, id="form"),
            pytest.param(
                {"form_class": _PermStep, "handler": _handler}, True, id="form-handler"
            ),
            pytest.param({"wizard_class": _BranchWizard}, False, id="wizard"),
            pytest.param({"handler": _handler}, False, id="handler"),
            pytest.param({}, False, id="none"),
        ],
    )
    def test_only_the_form_branch_defers_a_registered_perm(
        self, rf, meta, deferred
    ) -> None:
        add_perm(_EDIT, owns_group)
        request = _guarded_request(rf, _GuardUser(allowed=False))
        guard = ActionGuard(permissions=(_MODEL_PERM, _EDIT))
        meta = {**meta, "guard": guard, "file_path": _FORMS}
        with pytest.raises(PermissionDenied):
            FormActionDispatch._refuse_access(
                request, meta, None, "x", {}, guard=guard_rule(meta)
            )
        expected = (_MODEL_PERM,) if deferred else (_MODEL_PERM, _EDIT)
        assert request.user.received == [expected]

    def test_an_action_with_no_branch_is_a_400(self, rf) -> None:
        backend_meta = {"guard": None, "file_path": "/x/forms.py"}
        request = _guarded_request(rf, _GuardUser())
        response = FormActionDispatch.dispatch(
            None, request, "branchless", backend_meta
        )
        assert response.status_code == 400


class TestAnonymous:
    """An anonymous user is sent to log in before any predicate runs."""

    @pytest.mark.usefixtures("owned_group_form")
    def test_redirected_to_login(self, client_no_csrf, rule: _Rule) -> None:
        Group.objects.create(name="ada")
        response = _post_owned(client_no_csrf)
        assert response.status_code == 302
        assert response["Location"] == "/accounts/login/?next=/groups/ada/"
        assert rule.objects() == []

    def test_an_anonymous_request_at_the_object_stage(self, rf, rule: _Rule) -> None:
        request = _guarded_request(rf, AnonymousUser())
        denial = _check_access(
            request, _rule(ActionGuard(permissions=(_EDIT,)), form=True)
        )
        assert denial is not None
        assert denial.status_code == 302
        assert rule.objects() == []
