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
        for name in _BUFFER_NAMES:
            getattr(self, name).clear()

    def snapshot(self) -> "RegistrationDiagnostics":
        """Return an independent deep copy of every buffer."""
        return copy.deepcopy(self)

    def restore(self, snapshot: "RegistrationDiagnostics") -> None:
        """Replace the buffer contents in place with deep copies of a snapshot."""
        self.clear()
        for name in _BUFFER_NAMES:
            buffer = getattr(self, name)
            value = copy.deepcopy(getattr(snapshot, name))
            if isinstance(buffer, dict):
                buffer.update(value)
            else:
                buffer.extend(value)


# Resolved once: dataclasses.fields() is too slow for the per-test clear()
# the isolation helpers run.
_BUFFER_NAMES: tuple[str, ...] = tuple(
    spec.name for spec in fields(RegistrationDiagnostics)
)


registration_diagnostics = RegistrationDiagnostics()


__all__ = ["RegistrationDiagnostics", "registration_diagnostics"]
