from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from django.dispatch import Signal

from next.forms import RegistryFormActionBackend, form_action_manager
from next.forms.signals import (
    action_dispatched,
    action_registered,
    form_validation_failed,
)


PAGE_MODULE_FOR_FORM_TESTS = (
    Path(__file__).resolve().parent.parent / "site_pages" / "page.py"
).resolve()


@pytest.fixture()
def capture_action_registered() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    action_registered.connect(_listener)
    try:
        yield events
    finally:
        action_registered.disconnect(_listener)


@pytest.fixture()
def capture_action_dispatched() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    action_dispatched.connect(_listener)
    try:
        yield events
    finally:
        action_dispatched.disconnect(_listener)


@pytest.fixture()
def capture_form_validation_failed() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    form_validation_failed.connect(_listener)
    try:
        yield events
    finally:
        form_validation_failed.disconnect(_listener)


class TestActionRegisteredSignal:
    """``action_registered`` signal can be connected to and receives kwargs."""

    def test_signal_is_importable(self) -> None:
        """``action_registered`` is a Django Signal exported from ``next.forms.signals``."""
        assert isinstance(action_registered, Signal)

    def test_listener_receives_sent_event(
        self, capture_action_registered: list[dict[str, Any]]
    ) -> None:
        """Manually sending ``action_registered`` notifies connected listeners."""
        action_registered.send(sender=object, action_name="test_action")
        assert len(capture_action_registered) == 1

    def test_sender_is_passed_through(
        self, capture_action_registered: list[dict[str, Any]]
    ) -> None:
        """The sender argument is preserved in the captured event."""
        sentinel = object()
        action_registered.send(sender=sentinel, action_name="test_action")
        assert capture_action_registered[0]["sender"] is sentinel

    def test_kwargs_are_passed_through(
        self, capture_action_registered: list[dict[str, Any]]
    ) -> None:
        """Extra keyword arguments sent with the signal appear in captured events."""
        action_registered.send(sender=object, action_name="my_action", uid="abc123")
        assert capture_action_registered[0]["action_name"] == "my_action"
        assert capture_action_registered[0]["uid"] == "abc123"

    def test_multiple_sends_accumulate(
        self, capture_action_registered: list[dict[str, Any]]
    ) -> None:
        """Each send appends a new event to the captured list."""
        action_registered.send(sender=object, action_name="a")
        action_registered.send(sender=object, action_name="b")
        assert len(capture_action_registered) == 2

    def test_disconnected_after_fixture_teardown(self) -> None:
        """After the fixture tears down, the listener is no longer connected."""
        events: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs: object) -> None:
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
        self, capture_action_dispatched: list[dict[str, Any]]
    ) -> None:
        """Manually sending ``action_dispatched`` notifies connected listeners."""
        action_dispatched.send(sender=object)
        assert len(capture_action_dispatched) == 1

    def test_sender_is_passed_through(
        self, capture_action_dispatched: list[dict[str, Any]]
    ) -> None:
        """The sender argument is preserved in the captured event."""
        sentinel = object()
        action_dispatched.send(sender=sentinel)
        assert capture_action_dispatched[0]["sender"] is sentinel

    def test_kwargs_are_passed_through(
        self, capture_action_dispatched: list[dict[str, Any]]
    ) -> None:
        """Extra keyword arguments sent with the signal appear in captured events."""
        action_dispatched.send(sender=object, action_name="submit", status=200)
        assert capture_action_dispatched[0]["action_name"] == "submit"
        assert capture_action_dispatched[0]["status"] == 200

    def test_multiple_sends_accumulate(
        self, capture_action_dispatched: list[dict[str, Any]]
    ) -> None:
        """Each send appends a new event to the captured list."""
        action_dispatched.send(sender=object)
        action_dispatched.send(sender=object)
        assert len(capture_action_dispatched) == 2

    def test_disconnected_after_fixture_teardown(self) -> None:
        """After the fixture tears down, the listener is no longer connected."""
        events: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs: object) -> None:
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
        self, capture_form_validation_failed: list[dict[str, Any]]
    ) -> None:
        """Manually sending ``form_validation_failed`` notifies connected listeners."""
        form_validation_failed.send(sender=object)
        assert len(capture_form_validation_failed) == 1

    def test_sender_is_passed_through(
        self, capture_form_validation_failed: list[dict[str, Any]]
    ) -> None:
        """The sender argument is preserved in the captured event."""
        sentinel = object()
        form_validation_failed.send(sender=sentinel)
        assert capture_form_validation_failed[0]["sender"] is sentinel

    def test_kwargs_are_passed_through(
        self, capture_form_validation_failed: list[dict[str, Any]]
    ) -> None:
        """Extra keyword arguments sent with the signal appear in captured events."""
        form_validation_failed.send(sender=object, action_name="submit", errors=["e1"])
        assert capture_form_validation_failed[0]["action_name"] == "submit"
        assert capture_form_validation_failed[0]["errors"] == ["e1"]

    def test_multiple_sends_accumulate(
        self, capture_form_validation_failed: list[dict[str, Any]]
    ) -> None:
        """Each send appends a new event to the captured list."""
        form_validation_failed.send(sender=object)
        form_validation_failed.send(sender=object)
        assert len(capture_form_validation_failed) == 2

    def test_disconnected_after_fixture_teardown(self) -> None:
        """After the fixture tears down, the listener is no longer connected."""
        events: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs: object) -> None:
            events.append({"sender": sender})

        form_validation_failed.connect(_listener)
        form_validation_failed.disconnect(_listener)
        form_validation_failed.send(sender=object)
        assert len(events) == 0


class TestActionRegisteredWiring:
    """``action_registered`` fires when the framework registers an action."""

    def test_fires_from_register_action(
        self, capture_action_registered: list[dict[str, Any]]
    ) -> None:
        """Registering a new action via the backend emits the signal."""
        backend = RegistryFormActionBackend()
        backend.register_action("wired_action", lambda: None)
        events = [
            e
            for e in capture_action_registered
            if e.get("action_name") == "wired_action"
        ]
        assert len(events) == 1
        event = events[0]
        assert event["sender"] is RegistryFormActionBackend
        assert "uid" in event
        assert event["form_class"] is None
        assert event["namespace"] is None


@pytest.mark.django_db()
class TestActionDispatchedWiring:
    """``action_dispatched`` fires when the framework dispatches a real action."""

    def test_fires_on_successful_dispatch_without_form(
        self,
        client_no_csrf,
        capture_action_dispatched: list[dict[str, Any]],
    ) -> None:
        """A handler without form_class fires the signal with response_status."""
        url = form_action_manager.get_action_url("test_no_form")
        resp = client_no_csrf.post(url, data={})
        assert resp.status_code == 200
        assert len(capture_action_dispatched) == 1
        event = capture_action_dispatched[0]
        assert event["action_name"] == "test_no_form"
        assert event["response_status"] == 200
        assert isinstance(event["duration_ms"], float)
        assert event["duration_ms"] >= 0

    def test_fires_on_successful_dispatch_with_form(
        self,
        client_no_csrf,
        capture_action_dispatched: list[dict[str, Any]],
    ) -> None:
        """A valid bound form fires the signal after the handler runs."""
        url = form_action_manager.get_action_url("test_redirect")
        resp = client_no_csrf.post(
            url,
            data={
                "name": "Alice",
                "email": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        assert resp.status_code == 302
        assert len(capture_action_dispatched) == 1
        event = capture_action_dispatched[0]
        assert event["action_name"] == "test_redirect"
        assert event["response_status"] == 302

    def test_does_not_fire_on_invalid_form(
        self,
        client_no_csrf,
        capture_action_dispatched: list[dict[str, Any]],
    ) -> None:
        """An invalid form never reaches the handler, so no dispatched signal."""
        url = form_action_manager.get_action_url("test_submit")
        client_no_csrf.post(
            url,
            data={
                "name": "",
                "_next_form_page": str(PAGE_MODULE_FOR_FORM_TESTS),
            },
            follow=False,
        )
        assert capture_action_dispatched == []


@pytest.mark.django_db()
class TestFormValidationFailedWiring:
    """``form_validation_failed`` fires when validation fails during dispatch."""

    def test_fires_on_invalid_form_with_error_payload(
        self,
        client_no_csrf,
        capture_form_validation_failed: list[dict[str, Any]],
    ) -> None:
        """An invalid POST fires the signal with action_name, errors, fields."""
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
        assert len(capture_form_validation_failed) == 1
        event = capture_form_validation_failed[0]
        assert event["action_name"] == "test_submit"
        assert event["error_count"] >= 1
        assert "name" in event["field_names"]
