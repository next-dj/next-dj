"""Tests for next.forms check functions."""

import pytest

from next.forms import RegistryFormActionBackend
from next.forms.checks import (
    _action_collisions,
    check_form_action_collisions,
)


@pytest.fixture(autouse=True)
def _reset_collision_cache():
    _action_collisions.clear()
    yield
    _action_collisions.clear()


def _distinct_handler(tag: str):
    def _handler():
        return tag

    _handler.__qualname__ = f"distinct_handler_{tag}"
    return _handler


class TestFormActionCollisions:
    """check_form_action_collisions flags duplicate handlers on one action name."""

    def test_single_registration_is_clean(self) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action("solo", lambda: None)
        assert check_form_action_collisions() == []

    def test_duplicate_handlers_trigger_error(self) -> None:
        backend = RegistryFormActionBackend()
        backend.register_action("dup", _distinct_handler("a"))
        backend.register_action("dup", _distinct_handler("b"))
        errors = check_form_action_collisions()
        assert len(errors) == 1
        assert errors[0].id == "next.E041"
        assert "'dup'" in errors[0].msg

    def test_reregistration_of_same_handler_is_safe(self) -> None:
        backend = RegistryFormActionBackend()
        same = _distinct_handler("stable")
        backend.register_action("reload_me", same)
        backend.register_action("reload_me", same)
        assert check_form_action_collisions() == []

    def test_tracker_is_not_signal_receiver(self) -> None:
        """``register_action`` must not pay signal-dispatch cost for the tracker."""
        from next.forms.checks import _handler_fingerprint, record_possible_collision
        from next.forms.signals import action_registered

        connected_names = {
            getattr(receiver, "__name__", "")
            for _id, ref in action_registered.receivers
            for receiver in (ref(),)
            if receiver is not None
        }
        assert "record_possible_collision" not in connected_names
        # The helpers must still exist and remain callable from the
        # backend's direct path.
        assert callable(_handler_fingerprint)
        assert callable(record_possible_collision)

    def test_first_registration_records_no_collision(self) -> None:
        """Common case: a name registered once never touches the collision map."""
        backend = RegistryFormActionBackend()
        backend.register_action("only", _distinct_handler("x"))
        assert _action_collisions == {}
