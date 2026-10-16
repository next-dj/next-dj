import logging
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, ClassVar, Literal

import pytest
from django import forms as django_forms
from django.contrib.auth.models import Group
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.test import RequestFactory

from next.deps import DObject, UnboundObjectError
from next.forms import (
    ActionRegistration,
    Form,
    FormWizard,
    PermissionOutcome,
    RegistryFormActionBackend,
)
from next.forms.dispatch import FormActionDispatch
from next.forms.uid import ORIGIN_FIELD_NAME
from tests.support import routed, write_page


_FAKE_FILE = "/fake/myapp/forms.py"
_ACTION = "object_action"
_DISPATCH_LOGGER = "next.forms.dispatch"


@pytest.fixture()
def origins(tmp_path: Path) -> Generator[None, None, None]:
    """Route a page without URL kwargs and a page capturing an int `group`."""
    root = tmp_path / "pages"
    write_page(root, "plain")
    write_page(root, "[int:group]")
    with routed(root):
        yield


def _redirect() -> HttpResponseRedirect:
    return HttpResponseRedirect("/done/")


def _handler_object(group: DObject[Group]) -> HttpResponseRedirect:
    return _redirect()


def _handler_parent(
    group: DObject[Group, Literal["permissions"]],
) -> HttpResponseRedirect:
    return _redirect()


def _form_handler(form: Form) -> HttpResponseRedirect:
    return _redirect()


class _InitialObjectForm(Form):
    """Form whose `get_initial` loads a row by the origin URL kwarg."""

    name = django_forms.CharField(required=False)

    class Meta:
        """Unregistered, so the project-wide `DObject` checks never see it."""

        abstract = True

    @classmethod
    def get_initial(cls, group: DObject[Group]) -> dict[str, Any]:
        return {"name": group.name}


class _KwargsObjectForm(Form):
    """Form whose `get_form_kwargs` loads a row by the origin URL kwarg."""

    name = django_forms.CharField(required=False)

    class Meta:
        """Unregistered, so the project-wide `DObject` checks never see it."""

        abstract = True

    @classmethod
    def get_form_kwargs(
        cls, request: HttpRequest, group: DObject[Group]
    ) -> dict[str, Any]:
        return {"initial": {"name": group.name}}


class _ObjectStep(Form):
    """Single wizard step."""

    name = django_forms.CharField(required=False)


class _ObjectWizard(FormWizard):
    """Wizard whose view gate loads a row by the origin URL kwarg."""

    class Meta:
        """One step, unregistered so the global manager never sees it."""

        abstract = True
        steps: ClassVar = [("only", _ObjectStep)]

    @classmethod
    def check_permissions(cls, group: DObject[Group]) -> PermissionOutcome:
        return True


def _with_handler(handler: Callable[..., object]) -> ActionRegistration:
    return ActionRegistration(
        name=_ACTION, file_path=_FAKE_FILE, scope="shared", handler=handler
    )


def _with_form(form_class: type[Form]) -> ActionRegistration:
    return ActionRegistration(
        name=_ACTION,
        file_path=_FAKE_FILE,
        scope="shared",
        handler=_form_handler,
        form_class=form_class,
    )


def _with_wizard() -> ActionRegistration:
    return ActionRegistration(
        name=_ACTION, file_path=_FAKE_FILE, scope="shared", wizard_class=_ObjectWizard
    )


_LOADERS = (
    pytest.param(_with_handler(_handler_object), id="handler"),
    pytest.param(_with_form(_InitialObjectForm), id="get_initial"),
    pytest.param(_with_form(_KwargsObjectForm), id="get_form_kwargs"),
)
_BRANCHES = pytest.mark.parametrize(
    "registration", [*_LOADERS, pytest.param(_with_wizard(), id="wizard")]
)


def _dispatch(registration: ActionRegistration, origin: str | None) -> HttpResponse:
    backend = RegistryFormActionBackend()
    backend.register_action(registration)
    meta = backend.get_meta(_ACTION)
    assert meta is not None
    data = {} if origin is None else {ORIGIN_FIELD_NAME: origin}
    request = RequestFactory().post("/_next/form/x/", data)
    return FormActionDispatch.dispatch(backend, request, _ACTION, meta)


@pytest.mark.usefixtures("scoped_models", "origins")
class TestUnboundObjectOnDispatch:
    """A `DObject` kwarg the posted origin lacks is a bad request, not a crash."""

    @_BRANCHES
    def test_an_origin_without_the_kwarg_is_a_bad_request(
        self, registration: ActionRegistration, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_DISPATCH_LOGGER):
            response = _dispatch(registration, "/plain/")
        assert response.status_code == 400
        assert f"{_ACTION!r}" in response.content.decode()
        assert any(
            _ACTION in record.getMessage() and "'group'" in record.getMessage()
            for record in caplog.records
        )

    @pytest.mark.parametrize("registration", _LOADERS)
    def test_a_missing_origin_is_a_bad_request(
        self, registration: ActionRegistration
    ) -> None:
        assert _dispatch(registration, None).status_code == 400

    def test_a_missing_parent_still_raises(self) -> None:
        with pytest.raises(UnboundObjectError) as caught:
            _dispatch(_with_handler(_handler_parent), "/7/")
        assert caught.value.parent is True
        assert caught.value.key == "permissions"
