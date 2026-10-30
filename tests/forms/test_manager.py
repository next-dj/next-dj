from typing import ClassVar

import pytest
from django import forms as django_forms
from django.http import HttpRequest, HttpResponse

from next.errors import AsyncCallableError
from next.forms import Form, FormWizard
from next.forms.decorators import action
from next.forms.manager import form_action_manager


class _StepForm(django_forms.Form):
    """Plain step form a wizard below points at."""

    name = django_forms.CharField()


class TestAsyncActionRefusal:
    """An async handler or hook is refused when its action registers."""

    def test_async_handler_is_refused(self) -> None:
        async def archive(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        with pytest.raises(AsyncCallableError, match='@action handler "archive"'):
            action("archive_async")(archive)
        assert form_action_manager.default_backend.get_meta("archive_async") is None

    def test_async_bare_handler_is_refused(self) -> None:
        async def publish(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        with pytest.raises(AsyncCallableError, match='@action handler "publish"'):
            action(publish)

    @pytest.mark.parametrize(
        "hook",
        [
            "on_valid",
            "get_initial",
            "get_form_kwargs",
            "check_permissions",
            "has_object_permission",
        ],
    )
    def test_async_form_hook_is_refused(self, hook: str) -> None:
        async def body(*args: object, **kwargs: object) -> None:
            return None

        method = classmethod(body) if hook.startswith(("get_", "check_")) else body
        with pytest.raises(AsyncCallableError, match='Form hook "body"'):
            type(f"Async{hook.title()}Form", (Form,), {hook: method})

    def test_async_hook_of_an_action_form_class_is_refused(self) -> None:
        class DraftForm(Form):
            class Meta:
                abstract = True

            async def on_valid(self, request: HttpRequest) -> None:
                return None

        def save(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        with pytest.raises(AsyncCallableError, match='DraftForm hook "on_valid"'):
            action("save_draft", form_class=DraftForm)(save)

    def test_async_form_class_factory_is_refused(self) -> None:
        async def build_form(request: HttpRequest) -> type[Form]:
            return Form

        def save(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        with pytest.raises(
            AsyncCallableError, match='@action form_class factory "build_form"'
        ):
            action("save_factory", form_class=build_form)(save)

    def test_async_wizard_done_is_refused(self) -> None:
        with pytest.raises(AsyncCallableError, match='hook "done"'):

            class SignupWizard(FormWizard):
                class Meta:
                    steps: ClassVar = [("identity", _StepForm)]

                async def done(
                    self, request: HttpRequest, cleaned_data: dict[str, object]
                ) -> HttpResponse:
                    return HttpResponse()

    def test_sync_wizard_registers(self) -> None:
        class TourWizard(FormWizard):
            class Meta:
                steps: ClassVar = [("identity", _StepForm)]

            def done(
                self, request: HttpRequest, cleaned_data: dict[str, object]
            ) -> HttpResponse:
                return HttpResponse()

        assert form_action_manager.default_backend.get_meta("tour_wizard") is not None
