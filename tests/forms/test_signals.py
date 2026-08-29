from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

import pytest
from django import forms as django_forms
from django.dispatch import Signal
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict

from next.forms import ActionRegistration, Form, RegistryFormActionBackend
from next.forms.dispatch import FormActionDispatch
from next.forms.manager import form_action_manager
from next.forms.signals import (
    action_dispatched,
    action_registered,
    form_access_denied,
    form_validation_failed,
    wizard_completed,
    wizard_step_submitted,
)
from next.forms.wizard import FormWizard
from next.testing import SignalRecorder, capture_signals


_FAKE_FILE = "/fake/myapp/forms.py"


class SignalIdentityStep(Form):
    """First step of the signal wizard."""

    name = django_forms.CharField(max_length=100)


class SignalScopeStep(Form):
    """Second step of the signal wizard."""

    scope = django_forms.CharField(max_length=100)


class SignalWizard(FormWizard):
    """Two-step wizard used to assert wizard signals fire during dispatch."""

    class Meta:
        """Two ordered steps routed through the wizard backend."""

        steps: ClassVar = [("identity", SignalIdentityStep), ("scope", SignalScopeStep)]

    def done(self, request: HttpRequest, cleaned_data: dict) -> HttpResponseRedirect:
        """Finalise the wizard with a redirect."""
        return HttpResponseRedirect("/thanks/")


@pytest.fixture()
def capture_action_registered() -> Generator[SignalRecorder, None, None]:
    """Record ``action_registered`` emissions."""
    with capture_signals(action_registered) as recorder:
        yield recorder


@pytest.fixture()
def capture_action_dispatched() -> Generator[SignalRecorder, None, None]:
    """Record ``action_dispatched`` emissions."""
    with capture_signals(action_dispatched) as recorder:
        yield recorder


@pytest.fixture()
def capture_form_validation_failed() -> Generator[SignalRecorder, None, None]:
    """Record ``form_validation_failed`` emissions."""
    with capture_signals(form_validation_failed) as recorder:
        yield recorder


@pytest.fixture()
def capture_wizard_step_submitted() -> Generator[SignalRecorder, None, None]:
    """Record ``wizard_step_submitted`` emissions."""
    with capture_signals(wizard_step_submitted) as recorder:
        yield recorder


@pytest.fixture()
def capture_wizard_completed() -> Generator[SignalRecorder, None, None]:
    """Record ``wizard_completed`` emissions."""
    with capture_signals(wizard_completed) as recorder:
        yield recorder


@pytest.fixture()
def capture_form_access_denied() -> Generator[SignalRecorder, None, None]:
    """Record ``form_access_denied`` emissions."""
    with capture_signals(form_access_denied) as recorder:
        yield recorder


def _post_wizard_step(client, step: str, data: dict):
    """POST one wizard step through the dispatch client with the tag's hidden field."""
    url = form_action_manager.get_action_url("signal_wizard")
    payload = {"_next_form_origin": f"/request/{step}/", **data}
    return client.post(url, data=payload, follow=False)


class TestActionRegisteredSignal:
    """``action_registered`` signal can be connected to and receives kwargs."""

    def test_signal_is_importable(self) -> None:
        """``action_registered`` is a Django Signal exported from ``next.forms.signals``."""
        assert isinstance(action_registered, Signal)

    def test_listener_receives_sent_event(
        self, capture_action_registered: SignalRecorder
    ) -> None:
        """Manually sending ``action_registered`` notifies connected listeners."""
        action_registered.send(sender=object, action_name="test_action")
        assert len(capture_action_registered) == 1

    def test_sender_is_passed_through(
        self, capture_action_registered: SignalRecorder
    ) -> None:
        """The sender argument is preserved in the captured event."""
        sentinel = object()
        action_registered.send(sender=sentinel, action_name="test_action")
        assert capture_action_registered.events[0].sender is sentinel

    def test_kwargs_are_passed_through(
        self, capture_action_registered: SignalRecorder
    ) -> None:
        """Extra keyword arguments sent with the signal appear in captured events."""
        action_registered.send(sender=object, action_name="my_action", uid="abc123")
        assert capture_action_registered.events[0].kwargs["action_name"] == "my_action"
        assert capture_action_registered.events[0].kwargs["uid"] == "abc123"

    def test_multiple_sends_accumulate(
        self, capture_action_registered: SignalRecorder
    ) -> None:
        """Each send appends a new event to the captured list."""
        action_registered.send(sender=object, action_name="a")
        action_registered.send(sender=object, action_name="b")
        assert len(capture_action_registered) == 2

    def test_disconnected_after_fixture_teardown(self) -> None:
        """After the fixture tears down, the listener is no longer connected."""
        events: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs) -> None:
            events.append({"sender": sender})

        action_registered.connect(_listener)
        action_registered.disconnect(_listener)
        action_registered.send(sender=object)
        assert len(events) == 0


class TestActionDispatchedSignal:
    """``action_dispatched`` signal can be connected to and receives kwargs."""

    def test_signal_is_importable(self) -> None:
        """``action_dispatched`` is a Django Signal exported from ``next.forms.signals``."""
        assert isinstance(action_dispatched, Signal)

    def test_listener_receives_sent_event(
        self, capture_action_dispatched: SignalRecorder
    ) -> None:
        """Manually sending ``action_dispatched`` notifies connected listeners."""
        action_dispatched.send(sender=object)
        assert len(capture_action_dispatched) == 1

    def test_sender_is_passed_through(
        self, capture_action_dispatched: SignalRecorder
    ) -> None:
        """The sender argument is preserved in the captured event."""
        sentinel = object()
        action_dispatched.send(sender=sentinel)
        assert capture_action_dispatched.events[0].sender is sentinel

    def test_kwargs_are_passed_through(
        self, capture_action_dispatched: SignalRecorder
    ) -> None:
        """Extra keyword arguments sent with the signal appear in captured events."""
        action_dispatched.send(sender=object, action_name="submit", status=200)
        assert capture_action_dispatched.events[0].kwargs["action_name"] == "submit"
        assert capture_action_dispatched.events[0].kwargs["status"] == 200

    def test_multiple_sends_accumulate(
        self, capture_action_dispatched: SignalRecorder
    ) -> None:
        """Each send appends a new event to the captured list."""
        action_dispatched.send(sender=object)
        action_dispatched.send(sender=object)
        assert len(capture_action_dispatched) == 2

    def test_disconnected_after_fixture_teardown(self) -> None:
        """After the fixture tears down, the listener is no longer connected."""
        events: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs) -> None:
            events.append({"sender": sender})

        action_dispatched.connect(_listener)
        action_dispatched.disconnect(_listener)
        action_dispatched.send(sender=object)
        assert len(events) == 0


class TestFormValidationFailedSignal:
    """``form_validation_failed`` signal can be connected to and receives kwargs."""

    def test_signal_is_importable(self) -> None:
        """``form_validation_failed`` is a Django Signal exported from ``next.forms.signals``."""
        assert isinstance(form_validation_failed, Signal)

    def test_listener_receives_sent_event(
        self, capture_form_validation_failed: SignalRecorder
    ) -> None:
        """Manually sending ``form_validation_failed`` notifies connected listeners."""
        form_validation_failed.send(sender=object)
        assert len(capture_form_validation_failed) == 1

    def test_sender_is_passed_through(
        self, capture_form_validation_failed: SignalRecorder
    ) -> None:
        """The sender argument is preserved in the captured event."""
        sentinel = object()
        form_validation_failed.send(sender=sentinel)
        assert capture_form_validation_failed.events[0].sender is sentinel

    def test_kwargs_are_passed_through(
        self, capture_form_validation_failed: SignalRecorder
    ) -> None:
        """Extra keyword arguments sent with the signal appear in captured events."""
        form_validation_failed.send(sender=object, action_name="submit", errors=["e1"])
        assert (
            capture_form_validation_failed.events[0].kwargs["action_name"] == "submit"
        )
        assert capture_form_validation_failed.events[0].kwargs["errors"] == ["e1"]

    def test_multiple_sends_accumulate(
        self, capture_form_validation_failed: SignalRecorder
    ) -> None:
        """Each send appends a new event to the captured list."""
        form_validation_failed.send(sender=object)
        form_validation_failed.send(sender=object)
        assert len(capture_form_validation_failed) == 2

    def test_disconnected_after_fixture_teardown(self) -> None:
        """After the fixture tears down, the listener is no longer connected."""
        events: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs) -> None:
            events.append({"sender": sender})

        form_validation_failed.connect(_listener)
        form_validation_failed.disconnect(_listener)
        form_validation_failed.send(sender=object)
        assert len(events) == 0


class TestFormAccessDeniedSignal:
    """``form_access_denied`` connects, receives kwargs, and emits lazily."""

    def test_signal_is_importable(self) -> None:
        """``form_access_denied`` is a Django Signal exported from the module."""
        assert isinstance(form_access_denied, Signal)

    def test_listener_receives_sent_event(
        self, capture_form_access_denied: SignalRecorder
    ) -> None:
        """Sending ``form_access_denied`` notifies a connected listener."""
        form_access_denied.send(sender=object)
        assert len(capture_form_access_denied) == 1

    def test_payload_carries_layer_and_reason(
        self, capture_form_access_denied: SignalRecorder
    ) -> None:
        """The denial payload preserves layer and reason for receivers."""
        form_access_denied.send(
            sender=object, action_name="edit", uid="u1", layer="object", reason="denied"
        )
        event = capture_form_access_denied.events[0]
        assert event.kwargs["action_name"] == "edit"
        assert event.kwargs["uid"] == "u1"
        assert event.kwargs["layer"] == "object"
        assert event.kwargs["reason"] == "denied"

    def test_lazy_emit_has_no_receivers_by_default(self) -> None:
        """With no listener connected the signal reports an empty receiver list."""
        assert form_access_denied.receivers == []

    def test_disconnected_after_fixture_teardown(self) -> None:
        """After disconnecting, the listener is no longer notified."""
        events: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs) -> None:
            events.append({"sender": sender})

        form_access_denied.connect(_listener)
        form_access_denied.disconnect(_listener)
        form_access_denied.send(sender=object)
        assert len(events) == 0


class TestActionRegisteredWiring:
    """``action_registered`` fires when the framework registers an action."""

    def test_fires_from_register_action(
        self, capture_action_registered: SignalRecorder
    ) -> None:
        """Registering a new action via the backend emits the signal."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="wired_action",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        events = [
            e
            for e in capture_action_registered
            if e.kwargs.get("action_name") == "wired_action"
        ]
        assert len(events) == 1
        event = events[0]
        assert event.sender is RegistryFormActionBackend
        assert "uid" in event.kwargs
        assert event.kwargs["form_class"] is None
        assert event.kwargs["wizard_class"] is None
        assert "namespace" not in event.kwargs
        assert event.kwargs["file_path"] == _FAKE_FILE
        assert event.kwargs["scope"] == "shared"

    def test_fires_with_form_class(
        self, capture_action_registered: SignalRecorder
    ) -> None:
        """Registering a form_class action fires the signal with form_class set."""
        backend = RegistryFormActionBackend()

        class MySignalForm(django_forms.Form):
            name = django_forms.CharField()

        backend.register_action(
            ActionRegistration(
                name="form_signal_test",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=MySignalForm,
            )
        )
        events = [
            e
            for e in capture_action_registered
            if e.kwargs.get("action_name") == "form_signal_test"
        ]
        assert len(events) == 1
        assert events[0].kwargs["form_class"] is MySignalForm
        assert events[0].kwargs["wizard_class"] is None

    def test_fires_with_wizard_class(
        self, capture_action_registered: SignalRecorder
    ) -> None:
        """Registering a wizard action fires the signal with wizard_class set."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="wizard_signal_test",
                file_path=_FAKE_FILE,
                scope="shared",
                wizard_class=SignalWizard,
            )
        )
        events = [
            e
            for e in capture_action_registered
            if e.kwargs.get("action_name") == "wizard_signal_test"
        ]
        assert len(events) == 1
        assert events[0].kwargs["wizard_class"] is SignalWizard
        assert events[0].kwargs["form_class"] is None
        assert events[0].kwargs["handler"] is None


@pytest.mark.django_db()
class TestActionDispatchedWiring:
    """``action_dispatched`` fires when the framework dispatches a real action."""

    def test_fires_on_successful_dispatch_without_form(
        self, client_no_csrf, capture_action_dispatched: SignalRecorder
    ) -> None:
        """A handler without form_class fires the signal with response_status."""
        url = form_action_manager.get_action_url("test_no_form")
        resp = client_no_csrf.post(url, data={})
        assert resp.status_code == 200
        assert len(capture_action_dispatched) == 1
        event = capture_action_dispatched.events[0]
        assert event.kwargs["action_name"] == "test_no_form"
        assert event.kwargs["response_status"] == 200
        assert isinstance(event.kwargs["duration_ms"], float)
        assert event.kwargs["duration_ms"] >= 0
        assert event.kwargs["form"] is None
        assert event.kwargs["url_kwargs"] == {}
        meta = form_action_manager.get_action_meta("test_no_form")
        assert event.kwargs["uid"] == meta["uid"]
        assert event.kwargs["request"].path == url

    def test_fires_on_successful_dispatch_with_form(
        self, client_no_csrf, capture_action_dispatched: SignalRecorder
    ) -> None:
        """A valid bound form fires the signal after on_valid runs."""
        url = form_action_manager.get_action_url("simple_form_redirect")
        resp = client_no_csrf.post(url, data={"name": "Alice"}, follow=False)
        assert resp.status_code == 302
        assert len(capture_action_dispatched) == 1
        event = capture_action_dispatched.events[0]
        assert event.kwargs["action_name"] == "simple_form_redirect"
        assert event.kwargs["response_status"] == 302
        assert isinstance(event.kwargs["form"], Form)
        assert event.kwargs["form"].cleaned_data["name"] == "Alice"
        assert event.kwargs["url_kwargs"] == {}
        meta = form_action_manager.get_action_meta("simple_form_redirect")
        assert event.kwargs["uid"] == meta["uid"]
        assert event.kwargs["request"].method == "POST"

    def test_payload_includes_url_kwargs_from_resolved_origin(
        self, client_no_csrf, capture_action_dispatched: SignalRecorder
    ) -> None:
        """The resolved origin's typed URL kwargs surface as `url_kwargs`."""
        url = form_action_manager.get_action_url("test_no_form")
        client_no_csrf.post(url, data={"_next_form_origin": "/items/42/"})
        assert len(capture_action_dispatched) == 1
        assert capture_action_dispatched.events[0].kwargs["url_kwargs"] == {"id": 42}

    def test_does_not_fire_on_invalid_form(
        self, client_no_csrf, capture_action_dispatched: SignalRecorder
    ) -> None:
        """An invalid form never reaches the handler, so no dispatched signal."""
        url = form_action_manager.get_action_url("simple_form")
        client_no_csrf.post(
            url, data={"name": "", "_next_form_origin": "/"}, follow=False
        )
        assert len(capture_action_dispatched) == 0


@pytest.mark.django_db()
class TestFormValidationFailedWiring:
    """``form_validation_failed`` fires when validation fails during dispatch."""

    def test_fires_on_invalid_form_with_error_payload(
        self, client_no_csrf, capture_form_validation_failed: SignalRecorder
    ) -> None:
        """An invalid POST fires the signal with action_name, errors, fields."""
        url = form_action_manager.get_action_url("simple_form")
        resp = client_no_csrf.post(
            url, data={"name": "", "_next_form_origin": "/"}, follow=False
        )
        assert resp.status_code == 200
        assert len(capture_form_validation_failed) == 1
        event = capture_form_validation_failed.events[0]
        assert event.kwargs["action_name"] == "simple_form"
        assert event.kwargs["error_count"] >= 1
        assert "name" in event.kwargs["field_names"]
        meta = form_action_manager.get_action_meta("simple_form")
        assert event.kwargs["uid"] == meta["uid"]
        assert event.kwargs["request"].path == url


class TestWizardSignalsImportable:
    """The wizard signals are exported Django Signals."""

    @pytest.mark.parametrize(
        "signal",
        [wizard_step_submitted, wizard_completed],
        ids=["step_submitted", "completed"],
    )
    def test_signal_is_importable(self, signal) -> None:
        """Each wizard signal is a Django Signal."""
        assert isinstance(signal, Signal)


@pytest.mark.django_db()
class TestWizardSignalsWiring:
    """Wizard signals fire as steps validate and the wizard finalises."""

    def test_step_submitted_fires_per_valid_step(
        self, client_no_csrf, capture_wizard_step_submitted: SignalRecorder
    ) -> None:
        """`wizard_step_submitted` fires once per valid step with its cleaned data."""
        _post_wizard_step(client_no_csrf, "identity", {"name": "Ada"})
        _post_wizard_step(client_no_csrf, "scope", {"scope": "ops"})
        assert len(capture_wizard_step_submitted) == 2
        first = capture_wizard_step_submitted.events[0]
        assert first.sender is SignalWizard
        assert "wizard_class" not in first.kwargs
        assert first.kwargs["step"] == "identity"
        assert first.kwargs["cleaned_data"] == {"name": "Ada"}
        meta = form_action_manager.get_action_meta("signal_wizard")
        assert first.kwargs["uid"] == meta["uid"]
        assert first.kwargs["request"].method == "POST"
        assert capture_wizard_step_submitted.events[1].kwargs["step"] == "scope"

    def test_completed_fires_once_with_merged_data(
        self, client_no_csrf, capture_wizard_completed: SignalRecorder
    ) -> None:
        """`wizard_completed` fires once after the last step's done with merged data."""
        _post_wizard_step(client_no_csrf, "identity", {"name": "Ada"})
        assert len(capture_wizard_completed) == 0
        _post_wizard_step(client_no_csrf, "scope", {"scope": "ops"})
        assert len(capture_wizard_completed) == 1
        event = capture_wizard_completed.events[0]
        assert event.sender is SignalWizard
        assert "wizard_class" not in event.kwargs
        assert event.kwargs["cleaned_data"] == {"name": "Ada", "scope": "ops"}
        meta = form_action_manager.get_action_meta("signal_wizard")
        assert event.kwargs["uid"] == meta["uid"]
        assert event.kwargs["request"].method == "POST"

    def test_action_dispatched_fires_per_step(
        self, client_no_csrf, capture_action_dispatched: SignalRecorder
    ) -> None:
        """`action_dispatched` fires for each wizard step dispatch."""
        _post_wizard_step(client_no_csrf, "identity", {"name": "Ada"})
        _post_wizard_step(client_no_csrf, "scope", {"scope": "ops"})
        wizard_events = [
            e
            for e in capture_action_dispatched
            if e.kwargs.get("action_name") == "signal_wizard"
        ]
        assert len(wizard_events) == 2

    def test_receivers_can_filter_by_wizard_sender(self, client_no_csrf) -> None:
        """`connect(sender=...)` scopes wizard signal receivers to one wizard."""
        matched: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []

        def on_matched(sender: object, **kwargs) -> None:
            matched.append({"sender": sender, **kwargs})

        def on_unmatched(sender: object, **kwargs) -> None:
            unmatched.append({"sender": sender, **kwargs})

        wizard_step_submitted.connect(on_matched, sender=SignalWizard)
        wizard_step_submitted.connect(on_unmatched, sender=FormWizard)
        try:
            _post_wizard_step(client_no_csrf, "identity", {"name": "Ada"})
        finally:
            wizard_step_submitted.disconnect(on_matched, sender=SignalWizard)
            wizard_step_submitted.disconnect(on_unmatched, sender=FormWizard)
        assert len(matched) == 1
        assert matched[0]["step"] == "identity"
        assert unmatched == []

    def test_form_validation_failed_fires_on_invalid_step(
        self,
        client_no_csrf,
        capture_form_validation_failed: SignalRecorder,
        capture_wizard_step_submitted: SignalRecorder,
    ) -> None:
        """An invalid step fires `form_validation_failed` and no step-submitted signal."""
        resp = _post_wizard_step(client_no_csrf, "identity", {"name": ""})
        assert resp.status_code == 200
        assert len(capture_form_validation_failed) == 1
        assert (
            capture_form_validation_failed.events[0].kwargs["action_name"]
            == "signal_wizard"
        )
        assert len(capture_wizard_step_submitted) == 0


class SenderProbeForm(Form):
    """Form whose validation failure drives the sender regression checks."""

    name = django_forms.CharField(max_length=100)


class SenderDeniedForm(Form):
    """Form whose view-level hook denies with a response, firing the audit signal."""

    name = django_forms.CharField(max_length=100)

    @classmethod
    def check_permissions(cls) -> HttpResponse:
        """Deny every caller with a ready-made response."""
        return HttpResponse("denied", status=403)


@contextmanager
def _dispatch_receiver(signal: Signal) -> Iterator[list[dict[str, Any]]]:
    """Connect a receiver filtered on the `FormActionDispatch` sender."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs) -> None:
        events.append({"sender": sender, **kwargs})

    signal.connect(_listener, sender=FormActionDispatch)
    try:
        yield events
    finally:
        signal.disconnect(_listener, sender=FormActionDispatch)


def _dispatch_probe(
    backend: RegistryFormActionBackend, request: HttpRequest, name: str
) -> HttpResponse:
    meta = backend.get_meta(name)
    assert meta is not None
    return FormActionDispatch.dispatch(backend, request, name, meta)


class TestDispatchSignalSender:
    """Receivers filtering on `sender=FormActionDispatch` still get every signal.

    The dispatch bodies live in the submodules of `next.forms.dispatch`, so
    the sender identity is the contract keeping those receivers wired.
    """

    def test_action_dispatched_reaches_a_sender_filtered_receiver(
        self, mock_http_request
    ) -> None:
        backend = RegistryFormActionBackend()

        def handler() -> str:
            return "ok"

        backend.register_action(
            ActionRegistration(
                name="sender_handler",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=handler,
            )
        )
        request = mock_http_request(method="POST", POST=QueryDict(mutable=True))
        with _dispatch_receiver(action_dispatched) as events:
            _dispatch_probe(backend, request, "sender_handler")
        assert len(events) == 1
        assert events[0]["sender"] is FormActionDispatch
        assert events[0]["action_name"] == "sender_handler"

    def test_form_validation_failed_reaches_a_sender_filtered_receiver(
        self, mock_http_request
    ) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="sender_invalid",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=SenderProbeForm,
            )
        )
        request = mock_http_request(method="POST", POST=QueryDict(mutable=True))
        with _dispatch_receiver(form_validation_failed) as events:
            response = _dispatch_probe(backend, request, "sender_invalid")
        assert response.status_code == 400
        assert len(events) == 1
        assert events[0]["sender"] is FormActionDispatch
        assert events[0]["field_names"] == ("name",)

    def test_form_access_denied_reaches_a_sender_filtered_receiver(
        self, mock_http_request
    ) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="sender_denied",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=SenderDeniedForm,
            )
        )
        request = mock_http_request(method="POST", POST=QueryDict(mutable=True))
        with _dispatch_receiver(form_access_denied) as events:
            response = _dispatch_probe(backend, request, "sender_denied")
        assert response.status_code == 403
        assert len(events) == 1
        assert events[0]["sender"] is FormActionDispatch
        assert events[0]["layer"] == "view"
        assert events[0]["reason"] == "response"
