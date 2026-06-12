from next.forms.registration import RegistrationDiagnostics


def _populated() -> RegistrationDiagnostics:
    diagnostics = RegistrationDiagnostics()
    diagnostics.outside_base_dir.append(("OuterForm", "/outside/forms.py"))
    diagnostics.invalid_meta_scope.append(("BadScopeForm", "global"))
    diagnostics.invalid_action_scope.append(("bad_handler", "global"))
    diagnostics.instance_from_url_unknown_field.append(
        ("SomeForm", "auth.Group", "missing")
    )
    diagnostics.instance_from_url_on_non_model_form.append("PlainForm")
    diagnostics.action_collisions["dup"] = {("mod.a", "handler_a")}
    diagnostics.action_applied_to_class.append("DecoratedClass")
    diagnostics.wizard_without_steps.append("EmptyWizard")
    return diagnostics


class TestClear:
    """clear empties every buffer in place."""

    def test_clear_empties_every_buffer(self) -> None:
        """All eight buffers are empty after clear."""
        diagnostics = _populated()
        diagnostics.clear()
        assert diagnostics.outside_base_dir == []
        assert diagnostics.invalid_meta_scope == []
        assert diagnostics.invalid_action_scope == []
        assert diagnostics.instance_from_url_unknown_field == []
        assert diagnostics.instance_from_url_on_non_model_form == []
        assert diagnostics.action_collisions == {}
        assert diagnostics.action_applied_to_class == []
        assert diagnostics.wizard_without_steps == []

    def test_clear_keeps_buffer_identity(self) -> None:
        """The clear method mutates the containers instead of rebinding them."""
        diagnostics = _populated()
        outside = diagnostics.outside_base_dir
        collisions = diagnostics.action_collisions
        diagnostics.clear()
        assert diagnostics.outside_base_dir is outside
        assert diagnostics.action_collisions is collisions


class TestSnapshot:
    """snapshot returns an independent copy of every buffer."""

    def test_snapshot_copies_contents(self) -> None:
        """The snapshot holds equal contents in fresh containers."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        assert snapshot.outside_base_dir == diagnostics.outside_base_dir
        assert snapshot.action_collisions == diagnostics.action_collisions
        assert snapshot.outside_base_dir is not diagnostics.outside_base_dir
        assert snapshot.action_collisions is not diagnostics.action_collisions

    def test_mutating_live_state_does_not_affect_snapshot(self) -> None:
        """Appending to the live buffers leaves the snapshot untouched."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        diagnostics.outside_base_dir.append(("LateForm", "/late/forms.py"))
        diagnostics.action_collisions["dup"].add(("mod.b", "handler_b"))
        diagnostics.action_collisions["fresh"] = {("mod.c", "handler_c")}
        diagnostics.wizard_without_steps.append("LateWizard")
        assert snapshot.outside_base_dir == [("OuterForm", "/outside/forms.py")]
        assert snapshot.action_collisions == {"dup": {("mod.a", "handler_a")}}
        assert snapshot.wizard_without_steps == ["EmptyWizard"]


class TestRestore:
    """restore replaces the buffer contents in place from a snapshot."""

    def test_restore_brings_contents_back(self) -> None:
        """After clear and restore, every buffer matches the snapshot."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        diagnostics.clear()
        diagnostics.restore(snapshot)
        assert diagnostics.outside_base_dir == [("OuterForm", "/outside/forms.py")]
        assert diagnostics.invalid_meta_scope == [("BadScopeForm", "global")]
        assert diagnostics.invalid_action_scope == [("bad_handler", "global")]
        assert diagnostics.instance_from_url_unknown_field == [
            ("SomeForm", "auth.Group", "missing")
        ]
        assert diagnostics.instance_from_url_on_non_model_form == ["PlainForm"]
        assert diagnostics.action_collisions == {"dup": {("mod.a", "handler_a")}}
        assert diagnostics.action_applied_to_class == ["DecoratedClass"]
        assert diagnostics.wizard_without_steps == ["EmptyWizard"]

    def test_restore_drops_entries_added_after_snapshot(self) -> None:
        """Entries recorded after the snapshot vanish on restore."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        diagnostics.action_applied_to_class.append("LateClass")
        diagnostics.restore(snapshot)
        assert diagnostics.action_applied_to_class == ["DecoratedClass"]

    def test_restore_keeps_buffer_identity(self) -> None:
        """The restore method mutates the containers instead of rebinding them."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        outside = diagnostics.outside_base_dir
        collisions = diagnostics.action_collisions
        diagnostics.restore(snapshot)
        assert diagnostics.outside_base_dir is outside
        assert diagnostics.action_collisions is collisions

    def test_restore_copies_collision_sets(self) -> None:
        """Restored collision sets are independent of the snapshot's sets."""
        diagnostics = _populated()
        snapshot = diagnostics.snapshot()
        diagnostics.restore(snapshot)
        diagnostics.action_collisions["dup"].add(("mod.b", "handler_b"))
        assert snapshot.action_collisions == {"dup": {("mod.a", "handler_a")}}
