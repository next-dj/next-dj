from typing import Any, ClassVar

import pytest
from django import forms as django_forms
from django.contrib.auth.models import Group
from django.contrib.messages import MessageFailure, get_messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.functional import lazy

from next.forms import Form, FormWizard, ModelForm
from next.forms.base import _declared_success_url, _format_success_message
from next.forms.checks import check_success_message_framework
from next.forms.dispatch import FormActionDispatch, _send_success_message
from next.forms.manager import form_action_manager
from next.forms.signals import action_dispatched


def _computed_success_url() -> str:
    return "/computed/"


def _lazy_success_url() -> str:
    return "/lazy-done/"


class _FakeModelMeta:
    """Model options stub making an object pass _is_model_instance."""

    model = object


class FakeInstanceWithUrl:
    """Model-like object exposing get_absolute_url."""

    _meta = _FakeModelMeta()

    def get_absolute_url(self) -> str:
        return "/things/7/"


class MessageForm(Form):
    """Form flashing an interpolated success message."""

    name = django_forms.CharField(max_length=50)

    class Meta:
        """Message template over cleaned_data."""

        success_message = "Saved %(name)s."


class ErrorStatusMessageForm(Form):
    """Form whose on_valid returns an error status, gating the message."""

    name = django_forms.CharField(max_length=50)

    class Meta:
        """Message that must never flash."""

        success_message = "Never flashed."

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Return a server error to exercise the status gate."""
        return HttpResponse(status=500)


class SuccessUrlForm(Form):
    """Form with a declarative success redirect."""

    name = django_forms.CharField(max_length=50)

    class Meta:
        """Static success_url path."""

        success_url = "/after/"


class CallableSuccessUrlForm(Form):
    """Form whose success_url is computed at response time."""

    name = django_forms.CharField(max_length=50)

    class Meta:
        """Callable success_url."""

        success_url = _computed_success_url


class LazySuccessUrlForm(Form):
    """Form whose success_url is a lazy object."""

    name = django_forms.CharField(max_length=50)

    class Meta:
        """Lazy success_url evaluated via str()."""

        success_url = lazy(_lazy_success_url, str)()


class DefaultRedirectForm(Form):
    """Form keeping the default redirect-to-origin behaviour."""

    name = django_forms.CharField(max_length=50)


class InstanceReturnForm(Form):
    """Form whose on_valid returns a model-like instance."""

    name = django_forms.CharField(max_length=50)

    def on_valid(self, request: HttpRequest) -> object:
        """Return an instance so dispatch redirects via get_absolute_url."""
        return FakeInstanceWithUrl()


class GroupSuccessForm(ModelForm):
    """ModelForm with declarative redirect and message."""

    class Meta:
        """Bind to Group with success feedback declared."""

        model = Group
        fields: ClassVar[list[str]] = ["name"]
        success_url = "/groups/done/"
        success_message = "Group %(name)s saved."


class WizardIdentityMsgStep(Form):
    """First wizard step."""

    name = django_forms.CharField(max_length=50)

    class Meta:
        """Step form is abstract."""

        abstract = True


class WizardScopeMsgStep(Form):
    """Second wizard step."""

    scope = django_forms.CharField(max_length=50)

    class Meta:
        """Step form is abstract."""

        abstract = True


class MessageWizard(FormWizard):
    """Wizard flashing a message interpolated over merged cleaned data."""

    class Meta:
        """Two steps and a merged-data message."""

        steps: ClassVar = [
            ("identity", WizardIdentityMsgStep),
            ("scope", WizardScopeMsgStep),
        ]
        success_message = "Wizard saved %(name)s in %(scope)s."

    def done(
        self, request: HttpRequest, cleaned_data: dict[str, Any]
    ) -> HttpResponseRedirect:
        """Redirect to a thank-you page."""
        return HttpResponseRedirect("/thanks/")


class FailingDoneMessageWizard(FormWizard):
    """Wizard whose done returns an error status, gating the message."""

    class Meta:
        """One step and a message that must never flash."""

        steps: ClassVar = [("identity", WizardIdentityMsgStep)]
        success_message = "Never flashed."

    def done(self, request: HttpRequest, cleaned_data: dict[str, Any]) -> HttpResponse:
        """Return a server error to exercise the status gate."""
        return HttpResponse(status=500)


class _Flashes:
    """Success-message source stub returning a fixed message."""

    def get_success_message(self, cleaned_data: dict[str, Any]) -> str:
        return "stored"


class _Silent:
    """Success-message source stub returning an empty message."""

    def get_success_message(self, cleaned_data: dict[str, Any]) -> str:
        return ""


def _flashed(resp: HttpResponse) -> list[str]:
    return [m.message for m in get_messages(resp.wsgi_request)]


def _post_action(
    client, action_name: str, data: dict[str, str], origin: str = "/"
) -> HttpResponse:
    url = form_action_manager.get_action_url(action_name)
    return client.post(url, data={"_next_form_origin": origin, **data}, follow=False)


class TestFormatSuccessMessage:
    """_format_success_message interpolation rules."""

    @pytest.mark.parametrize(
        ("cls", "cleaned_data", "expected"),
        [
            pytest.param(object, {}, "", id="class-without-meta"),
            pytest.param(
                DefaultRedirectForm, {"name": "Ada"}, "", id="meta-without-message"
            ),
            pytest.param(
                MessageForm,
                {"name": "Ada"},
                "Saved Ada.",
                id="interpolates-cleaned-data",
            ),
        ],
    )
    def test_message_rendering(
        self, cls: type, cleaned_data: dict[str, str], expected: str
    ) -> None:
        assert _format_success_message(cls, cleaned_data) == expected


class TestDeclaredSuccessUrl:
    """_declared_success_url evaluation rules."""

    @pytest.mark.parametrize(
        ("cls", "expected"),
        [
            pytest.param(object, None, id="class-without-meta"),
            pytest.param(DefaultRedirectForm, None, id="meta-without-url"),
            pytest.param(SuccessUrlForm, "/after/", id="string-passes-through"),
            pytest.param(CallableSuccessUrlForm, "/computed/", id="callable-is-called"),
            pytest.param(LazySuccessUrlForm, "/lazy-done/", id="lazy-resolves-via-str"),
        ],
    )
    def test_url_evaluation(self, cls: type, expected: str | None) -> None:
        assert _declared_success_url(cls) == expected


class TestGetSuccessMessageDefaults:
    """Default get_success_message reads Meta on forms and wizards."""

    def test_form_default_interpolates_meta_template(self) -> None:
        form = MessageForm(data={"name": "Ada"})
        assert form.is_valid()
        assert form.get_success_message(form.cleaned_data) == "Saved Ada."

    def test_model_form_default_interpolates_meta_template(self) -> None:
        form = GroupSuccessForm()
        assert form.get_success_message({"name": "ops"}) == "Group ops saved."

    def test_wizard_default_interpolates_meta_template(self, rf) -> None:
        wizard = MessageWizard(request=rf.get("/request/identity/"))
        message = wizard.get_success_message({"name": "Ada", "scope": "ops"})
        assert message == "Wizard saved Ada in ops."


class TestSendSuccessMessage:
    """_send_success_message duck-typing and failure behaviour."""

    def test_source_without_hook_is_skipped(self, rf) -> None:
        _send_success_message(rf.post("/"), object(), {})

    def test_empty_message_is_skipped(self, rf) -> None:
        _send_success_message(rf.post("/"), _Silent(), {})

    def test_missing_messages_framework_raises(self, rf) -> None:
        with pytest.raises(MessageFailure):
            _send_success_message(rf.post("/"), _Flashes(), {})


@pytest.mark.django_db()
class TestSuccessMessageViaClient:
    """Meta.success_message flashes through django.contrib.messages."""

    def test_message_flashed_on_redirect(self, client_no_csrf) -> None:
        resp = _post_action(client_no_csrf, "message_form", {"name": "Ada"})
        assert resp.status_code == 302
        assert _flashed(resp) == ["Saved Ada."]

    def test_message_stored_unescaped(self, client_no_csrf) -> None:
        resp = _post_action(client_no_csrf, "message_form", {"name": "<b>Ada</b>"})
        assert _flashed(resp) == ["Saved <b>Ada</b>."]

    def test_invalid_submission_flashes_nothing(self, client_no_csrf) -> None:
        resp = _post_action(client_no_csrf, "message_form", {"name": ""})
        assert resp.status_code == 200
        assert _flashed(resp) == []

    def test_error_status_gates_the_message(self, client_no_csrf) -> None:
        resp = _post_action(
            client_no_csrf, "error_status_message_form", {"name": "Ada"}
        )
        assert resp.status_code == 500
        assert _flashed(resp) == []

    def test_form_without_message_flashes_nothing(self, client_no_csrf) -> None:
        resp = _post_action(client_no_csrf, "simple_form", {"name": "Ada"})
        assert _flashed(resp) == []

    def test_message_precedes_action_dispatched(self, client_no_csrf) -> None:
        seen: list[list[str] | None] = []

        def receiver(sender, request, **kwargs: object) -> None:
            storage = getattr(request, "_messages", None)
            seen.append(
                [m.message for m in storage._queued_messages]
                if storage is not None
                else None
            )

        action_dispatched.connect(receiver)
        try:
            _post_action(client_no_csrf, "message_form", {"name": "Ada"})
        finally:
            action_dispatched.disconnect(receiver)
        assert seen == [["Saved Ada."]]

    def test_wizard_message_only_flashes_after_done(self, client_no_csrf) -> None:
        url = form_action_manager.get_action_url("message_wizard")
        first = client_no_csrf.post(
            url,
            data={"_next_form_origin": "/request/identity/", "name": "Ada"},
            follow=False,
        )
        assert first.status_code == 302
        assert _flashed(first) == []
        second = client_no_csrf.post(
            url,
            data={"_next_form_origin": "/request/scope/", "scope": "ops"},
            follow=False,
        )
        assert second.url == "/thanks/"
        assert _flashed(second) == ["Wizard saved Ada in ops."]

    def test_failing_done_gates_the_wizard_message(self, client_no_csrf) -> None:
        url = form_action_manager.get_action_url("failing_done_message_wizard")
        resp = client_no_csrf.post(
            url,
            data={"_next_form_origin": "/request/identity/", "name": "Ada"},
            follow=False,
        )
        assert resp.status_code == 500
        assert _flashed(resp) == []


@pytest.mark.django_db()
class TestSuccessUrlViaClient:
    """Meta.success_url overrides the redirect-to-origin default."""

    @pytest.mark.parametrize(
        ("action_name", "expected_url"),
        [
            pytest.param("success_url_form", "/after/", id="static-beats-origin"),
            pytest.param("callable_success_url_form", "/computed/", id="callable"),
            pytest.param("lazy_success_url_form", "/lazy-done/", id="lazy"),
            pytest.param("default_redirect_form", "/", id="default-origin"),
        ],
    )
    def test_redirect_target(
        self, client_no_csrf, action_name: str, expected_url: str
    ) -> None:
        resp = _post_action(client_no_csrf, action_name, {"name": "Ada"})
        assert resp.status_code == 302
        assert resp.url == expected_url

    def test_model_form_saves_then_follows_success_url(self, client_no_csrf) -> None:
        resp = _post_action(client_no_csrf, "group_success_form", {"name": "ops"})
        assert resp.status_code == 302
        assert resp.url == "/groups/done/"
        assert Group.objects.filter(name="ops").exists()
        assert _flashed(resp) == ["Group ops saved."]

    def test_instance_return_redirects_via_get_absolute_url(
        self, client_no_csrf
    ) -> None:
        resp = _post_action(client_no_csrf, "instance_return_form", {"name": "Ada"})
        assert resp.status_code == 302
        assert resp.url == "/things/7/"


class TestModelInstanceNormalisation:
    """ensure_http_response turns model instances into canonical redirects."""

    def test_instance_with_get_absolute_url_redirects(self) -> None:
        resp = FormActionDispatch.ensure_http_response(FakeInstanceWithUrl())
        assert resp.status_code == 302
        assert resp.url == "/things/7/"

    def test_instance_without_canonical_url_falls_back_to_url_sniff(self) -> None:
        instance = type(
            "WithUrlField", (), {"_meta": _FakeModelMeta(), "url": "/legacy/"}
        )()
        resp = FormActionDispatch.ensure_http_response(instance)
        assert resp.status_code == 302
        assert resp.url == "/legacy/"

    def test_instance_without_any_url_warns(self) -> None:
        bare = type("Bare", (), {"_meta": _FakeModelMeta()})()
        with pytest.warns(RuntimeWarning, match="unsupported"):
            resp = FormActionDispatch.ensure_http_response(bare)
        assert resp.status_code == 204


class TestSuccessMessageFrameworkCheck:
    """next.W061 warns on success_message without the messages framework."""

    def test_full_framework_is_clean(self) -> None:
        assert check_success_message_framework() == []

    def test_missing_middleware_warns(self, settings) -> None:
        settings.MIDDLEWARE = [
            mw
            for mw in settings.MIDDLEWARE
            if mw != "django.contrib.messages.middleware.MessageMiddleware"
        ]
        messages = check_success_message_framework()
        assert messages
        assert all(m.id == "next.W061" for m in messages)
        flagged = " ".join(m.msg for m in messages)
        assert "message_form" in flagged
        assert "message_wizard" in flagged
        assert "default_redirect_form" not in flagged

    def test_missing_app_warns(self, settings) -> None:
        settings.INSTALLED_APPS = [
            app for app in settings.INSTALLED_APPS if app != "django.contrib.messages"
        ]
        assert any(m.id == "next.W061" for m in check_success_message_framework())
