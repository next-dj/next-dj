"""Diagnostics buffers accumulated during form registration."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class RegistrationDiagnostics:
    """Registration problems collected for the forms system checks."""

    outside_base_dir: list[tuple[str, str]] = field(default_factory=list)
    invalid_meta_scope: list[tuple[str, str]] = field(default_factory=list)
    invalid_action_scope: list[tuple[str, str]] = field(default_factory=list)
    instance_from_url_unknown_field: list[tuple[str, str, str]] = field(
        default_factory=list
    )
    instance_from_url_on_non_model_form: list[str] = field(default_factory=list)
    action_collisions: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    action_applied_to_class: list[str] = field(default_factory=list)
    wizard_without_steps: list[str] = field(default_factory=list)

    def clear(self) -> None:
        """Empty every buffer in place."""
        self.outside_base_dir.clear()
        self.invalid_meta_scope.clear()
        self.invalid_action_scope.clear()
        self.instance_from_url_unknown_field.clear()
        self.instance_from_url_on_non_model_form.clear()
        self.action_collisions.clear()
        self.action_applied_to_class.clear()
        self.wizard_without_steps.clear()

    def snapshot(self) -> "RegistrationDiagnostics":
        """Return an independent copy of every buffer."""
        return RegistrationDiagnostics(
            outside_base_dir=list(self.outside_base_dir),
            invalid_meta_scope=list(self.invalid_meta_scope),
            invalid_action_scope=list(self.invalid_action_scope),
            instance_from_url_unknown_field=list(self.instance_from_url_unknown_field),
            instance_from_url_on_non_model_form=list(
                self.instance_from_url_on_non_model_form
            ),
            action_collisions={
                name: set(fingerprints)
                for name, fingerprints in self.action_collisions.items()
            },
            action_applied_to_class=list(self.action_applied_to_class),
            wizard_without_steps=list(self.wizard_without_steps),
        )

    def restore(self, snapshot: "RegistrationDiagnostics") -> None:
        """Replace the buffer contents in place from a snapshot."""
        self.clear()
        self.outside_base_dir.extend(snapshot.outside_base_dir)
        self.invalid_meta_scope.extend(snapshot.invalid_meta_scope)
        self.invalid_action_scope.extend(snapshot.invalid_action_scope)
        self.instance_from_url_unknown_field.extend(
            snapshot.instance_from_url_unknown_field
        )
        self.instance_from_url_on_non_model_form.extend(
            snapshot.instance_from_url_on_non_model_form
        )
        self.action_collisions.update(
            {
                name: set(fingerprints)
                for name, fingerprints in snapshot.action_collisions.items()
            }
        )
        self.action_applied_to_class.extend(snapshot.action_applied_to_class)
        self.wizard_without_steps.extend(snapshot.wizard_without_steps)


registration_diagnostics = RegistrationDiagnostics()


__all__ = ["RegistrationDiagnostics", "registration_diagnostics"]
