from contextlib import AbstractContextManager
from typing import ClassVar

import pytest
from django import forms as django_forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form, FormWizard, ModelForm, PermissionOutcome, action
from next.forms.dispatch import FormActionDispatch
from next.forms.manager import form_action_manager
from next.forms.signals import form_access_denied
from next.forms.uid import ORIGIN_FIELD_NAME
from next.transactions import AtomicScope, request_atomic


pytestmark = pytest.mark.django_db(transaction=True)

User = get_user_model()


class _Audit:
    """A receiver that writes one row at once and another one when its block commits."""

    def __init__(self) -> None:
        self.atomic: list[bool] = []
        self.layers: list[tuple[str, str]] = []

    def __call__(self, sender: type, *, layer: str, reason: str, **_: object) -> None:
        self.atomic.append(connection.in_atomic_block)
        self.layers.append((layer, reason))
        Group.objects.create(name="audit-now")
        transaction.on_commit(lambda: Group.objects.create(name="audit-on-commit"))

    @staticmethod
    def kept() -> set[str]:
        names = Group.objects.filter(name__startswith="audit-").values_list("name")
        return {name for (name,) in names}


_BOTH = {"audit-now", "audit-on-commit"}


@pytest.fixture()
def audit():
    receiver = _Audit()
    form_access_denied.connect(receiver, sender=FormActionDispatch, weak=False)
    yield receiver
    form_access_denied.disconnect(receiver, sender=FormActionDispatch)


@pytest.fixture()
def opened(monkeypatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def recording(scope: AtomicScope) -> AbstractContextManager[object]:
        calls.append(scope.aliases)
        return request_atomic(scope)

    monkeypatch.setattr("next.forms.dispatch.request_atomic", recording)
    return calls


@pytest.fixture()
def member(client_no_csrf):
    client_no_csrf.force_login(User.objects.create_user("ada"))
    return client_no_csrf


def _post(client, action_name: str, origin: str = "/", **data: str) -> HttpResponse:
    url = form_action_manager.get_action_url(action_name)
    return client.post(url, data={ORIGIN_FIELD_NAME: origin, **data}, follow=False)


class _DenialStep(Form):
    """A wizard step with one field."""

    name = django_forms.CharField(max_length=20)


class TestViewLevelDenial:
    """A refused `check_permissions` answers before any transaction opens."""

    def test_a_form_keeps_what_the_receiver_wrote(self, member, audit, opened) -> None:
        class DenialViewForm(Form):
            name = django_forms.CharField(max_length=20)

            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                return False

            def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
                return HttpResponseRedirect("/done/")

        response = _post(member, "denial_view_form", name="x")
        assert response.status_code == 403
        assert audit.layers == [("view", "denied")]
        assert audit.atomic == [False]
        assert audit.kept() == _BOTH
        assert opened == []

    def test_a_model_form_keeps_what_the_receiver_wrote(
        self, member, audit, opened
    ) -> None:
        class DenialViewGroupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]

            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                return False

        response = _post(member, "denial_view_group_form", name="fresh")
        assert response.status_code == 403
        assert audit.atomic == [False]
        assert audit.kept() == _BOTH
        assert not Group.objects.filter(name="fresh").exists()
        assert opened == []

    def test_a_wizard_step_keeps_what_the_receiver_wrote(
        self, member, audit, opened
    ) -> None:
        class DenialViewWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("identity", _DenialStep)]

            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                return False

            def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponse:
                return HttpResponseRedirect("/done/")

        response = _post(member, "denial_view_wizard", "/request/identity/", name="x")
        assert response.status_code == 403
        assert audit.layers == [("view", "denied")]
        assert audit.atomic == [False]
        assert audit.kept() == _BOTH
        assert opened == []

    def test_a_raised_refusal_keeps_what_the_receiver_wrote(
        self, member, audit, opened
    ) -> None:
        class DenialRaisingForm(Form):
            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                raise PermissionDenied

        response = _post(member, "denial_raising_form")
        assert response.status_code == 403
        assert audit.layers == [("view", "raised")]
        assert audit.atomic == [False]
        assert audit.kept() == _BOTH
        assert opened == []

    def test_an_allowed_form_runs_in_the_transaction(
        self, member, audit, opened
    ) -> None:
        events: list[bool] = []

        class AllowedViewGroupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]

            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                events.append(connection.in_atomic_block)
                return True

            def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
                events.append(connection.in_atomic_block)
                self.save()
                return HttpResponseRedirect("/done/")

        response = _post(member, "allowed_view_group_form", name="fresh")
        assert response.status_code == 302
        assert events == [False, True]
        assert Group.objects.filter(name="fresh").exists()
        assert audit.layers == []
        assert opened == [("default",)]


class TestObjectLevelDenial:
    """An object check runs on the row inside the transaction and is announced after."""

    @pytest.fixture()
    def ada(self) -> Group:
        return Group.objects.create(name="ada")

    def test_the_receiver_runs_after_the_rollback(
        self, member, audit, opened, ada
    ) -> None:
        class DenialObjectGroupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name"

            def has_object_permission(self) -> PermissionOutcome:
                Group.objects.create(name="from-check")
                return self.instance.pk != ada.pk

        response = _post(member, "denial_object_group_form", "/groups/ada/", name="x")
        assert response.status_code == 403
        assert audit.layers == [("object", "denied")]
        assert audit.atomic == [False]
        assert audit.kept() == _BOTH
        assert not Group.objects.filter(name="from-check").exists()
        assert opened == [("default",)]
        ada.refresh_from_db()
        assert ada.name == "ada"

    def test_a_refusing_response_is_announced_after_the_commit(
        self, member, audit, ada
    ) -> None:
        class AnsweringObjectGroupForm(ModelForm):
            class Meta:
                model = Group
                fields: ClassVar[list[str]] = ["name"]
                instance_from_url = "name"

            def has_object_permission(self) -> PermissionOutcome:
                return HttpResponse(status=409)

        response = _post(
            member, "answering_object_group_form", "/groups/ada/", name="x"
        )
        assert response.status_code == 409
        assert audit.layers == [("object", "response")]
        assert audit.atomic == [False]
        assert audit.kept() == _BOTH

    @pytest.mark.parametrize(
        ("outcome", "status", "reason"),
        [(False, 403, "denied"), (HttpResponse(status=409), 409, "response")],
        ids=["denied", "response"],
    )
    def test_a_factory_class_is_checked_inside_the_transaction(
        self,
        member,
        audit,
        opened,
        outcome: PermissionOutcome,
        status: int,
        reason: str,
    ) -> None:
        checked: list[bool] = []

        class DenialFactoryForm(Form):
            @classmethod
            def check_permissions(cls) -> PermissionOutcome:
                checked.append(connection.in_atomic_block)
                return outcome

            class Meta:
                abstract = True

        @action("denial_factory_action", form_class=lambda: DenialFactoryForm)
        def denial_factory_action(form: Form) -> HttpResponse:
            return HttpResponse("ran")

        response = _post(member, "denial_factory_action")
        assert response.status_code == status
        assert checked == [True]
        assert audit.layers == [("view", reason)]
        assert audit.atomic == [False]
        assert audit.kept() == _BOTH
        assert opened == [("default",)]
