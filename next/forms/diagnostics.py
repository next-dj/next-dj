"""Diagnostics buffers accumulated during form registration."""

import copy
from dataclasses import dataclass, field, fields


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
    shared_name_collisions: dict[str, set[str]] = field(default_factory=dict)
    action_applied_to_class: list[str] = field(default_factory=list)
    wizard_without_steps: list[str] = field(default_factory=list)

    def clear(self) -> None:
        """Empty every buffer in place."""
        for spec in fields(self):
            getattr(self, spec.name).clear()

    def snapshot(self) -> "RegistrationDiagnostics":
        """Return an independent deep copy of every buffer."""
        return copy.deepcopy(self)

    def restore(self, snapshot: "RegistrationDiagnostics") -> None:
        """Replace the buffer contents in place with deep copies of a snapshot."""
        self.clear()
        for spec in fields(self):
            buffer = getattr(self, spec.name)
            value = copy.deepcopy(getattr(snapshot, spec.name))
            if isinstance(buffer, dict):
                buffer.update(value)
            else:
                buffer.extend(value)


registration_diagnostics = RegistrationDiagnostics()


__all__ = ["RegistrationDiagnostics", "registration_diagnostics"]
