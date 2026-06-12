import copy
import dataclasses

import pytest

from next.forms.diagnostics import RegistrationDiagnostics


_SAMPLES: dict[str, list[object] | dict[str, set[object]]] = {
    "outside_base_dir": [("OuterForm", "/outside/forms.py")],
    "invalid_meta_scope": [("BadScopeForm", "global")],
    "invalid_action_scope": [("bad_handler", "global")],
    "instance_from_url_unknown_field": [("SomeForm", "auth.Group", "missing")],
    "instance_from_url_on_non_model_form": ["PlainForm"],
    "action_collisions": {"dup": {("mod.a", "handler_a")}},
    "shared_name_collisions": {"contact_form": {"app_a.forms", "app_b.forms"}},
    "action_applied_to_class": ["DecoratedClass"],
    "wizard_without_steps": ["EmptyWizard"],
}

_FIELD_NAMES = sorted(_SAMPLES)


def _populated() -> RegistrationDiagnostics:
    diagnostics = RegistrationDiagnostics()
    for name, sample in _SAMPLES.items():
        buffer = getattr(diagnostics, name)
        if isinstance(buffer, dict):
            buffer.update(copy.deepcopy(sample))
        else:
            buffer.extend(sample)
    return diagnostics


def test_samples_cover_every_field() -> None:
    """The sample table names exactly the declared diagnostics buffers."""
    declared = {spec.name for spec in dataclasses.fields(RegistrationDiagnostics)}
    assert set(_SAMPLES) == declared


class TestClear:
    """clear empties every buffer in place."""

    @pytest.mark.parametrize("field_name", _FIELD_NAMES)
    def test_clear_empties_buffer(self, field_name: str) -> None:
        """Each buffer is empty after clear."""
        diagnostics = _populated()
        diagnostics.clear()
        assert not getattr(diagnostics, field_name)

    @pytest.mark.parametrize("field_name", _FIELD_NAMES)
    def test_clear_keeps_buffer_identity(self, field_name: str) -> None:
        """The clear method mutates the containers instead of rebinding them."""
        diagnostics = _populated()
        buffer = getattr(diagnostics, field_name)
        diagnostics.clear()
        assert getattr(diagnostics, field_name) is buffer


class TestSnapshot:
    """snapshot returns an independent copy of every buffer."""

    @pytest.mark.parametrize("field_name", _FIELD_NAMES)
    def test_snapshot_copies_contents(self, field_name: str) -> None:
        """The snapshot holds equal contents in fresh containers."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        assert getattr(snapshot, field_name) == getattr(diagnostics, field_name)
        assert getattr(snapshot, field_name) is not getattr(diagnostics, field_name)

    def test_mutating_live_state_does_not_affect_snapshot(self) -> None:
        """Appending to the live buffers leaves the snapshot untouched."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        diagnostics.outside_base_dir.append(("LateForm", "/late/forms.py"))
        diagnostics.action_collisions["dup"].add(("mod.b", "handler_b"))
        diagnostics.action_collisions["fresh"] = {("mod.c", "handler_c")}
        diagnostics.shared_name_collisions["contact_form"].add("app_c.forms")
        diagnostics.wizard_without_steps.append("LateWizard")
        assert snapshot.outside_base_dir == [("OuterForm", "/outside/forms.py")]
        assert snapshot.action_collisions == {"dup": {("mod.a", "handler_a")}}
        assert snapshot.shared_name_collisions == {
            "contact_form": {"app_a.forms", "app_b.forms"}
        }
        assert snapshot.wizard_without_steps == ["EmptyWizard"]


class TestRestore:
    """restore replaces the buffer contents in place from a snapshot."""

    @pytest.mark.parametrize("field_name", _FIELD_NAMES)
    def test_restore_brings_contents_back(self, field_name: str) -> None:
        """After clear and restore, each buffer matches the sample table."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        diagnostics.clear()
        diagnostics.restore(snapshot)
        assert getattr(diagnostics, field_name) == _SAMPLES[field_name]

    def test_restore_drops_entries_added_after_snapshot(self) -> None:
        """Entries recorded after the snapshot vanish on restore."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        diagnostics.action_applied_to_class.append("LateClass")
        diagnostics.restore(snapshot)
        assert diagnostics.action_applied_to_class == ["DecoratedClass"]

    @pytest.mark.parametrize("field_name", _FIELD_NAMES)
    def test_restore_keeps_buffer_identity(self, field_name: str) -> None:
        """The restore method mutates the containers instead of rebinding them."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        buffer = getattr(diagnostics, field_name)
        diagnostics.restore(snapshot)
        assert getattr(diagnostics, field_name) is buffer

    def test_restore_copies_collision_sets(self) -> None:
        """Restored collision sets are independent of the snapshot's sets."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        diagnostics.restore(snapshot)
        diagnostics.action_collisions["dup"].add(("mod.b", "handler_b"))
        diagnostics.shared_name_collisions["contact_form"].add("app_c.forms")
        assert snapshot.action_collisions == {"dup": {("mod.a", "handler_a")}}
        assert snapshot.shared_name_collisions == {
            "contact_form": {"app_a.forms", "app_b.forms"}
        }
