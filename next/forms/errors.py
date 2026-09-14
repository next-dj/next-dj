"""Exceptions the forms area raises for a lookup or a value it cannot serve."""

import difflib
from functools import cached_property
from typing import TYPE_CHECKING, Any, NamedTuple, override

from django.core.exceptions import ImproperlyConfigured


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path


class _LookupContext(NamedTuple):
    """What one failed action lookup knew about itself when it was raised."""

    name: str
    page_path: "str | None"
    candidates: "Callable[[], Iterable[str]] | Iterable[str]"
    registry_empty: bool


class FormActionNotFoundError(LookupError):
    """No registered form action matches the requested name."""

    def __init__(
        self,
        message: str | None = None,
        *,
        name: str = "",
        page_path: str | None = None,
        candidates: "Callable[[], Iterable[str]] | Iterable[str]" = (),
        registry_empty: bool = False,
    ) -> None:
        """Store the lookup context, deferring close-match work until rendered."""
        # The manager probes backends by catching this, so raising stays cheap.
        # One record now, difflib and the message only when rendered.
        self._context = _LookupContext(name, page_path, candidates, registry_empty)
        if message is None:
            super().__init__()
        else:
            super().__init__(message)

    @property
    def name(self) -> str:
        """Return the action name the failed lookup asked for."""
        return self._context.name

    @property
    def page_path(self) -> str | None:
        """Return the page scope the lookup searched, when any."""
        return self._context.page_path

    @property
    def registry_empty(self) -> bool:
        """Return True when no actions were registered at raise time."""
        return self._context.registry_empty

    @property
    def candidates(self) -> tuple[str, ...]:
        """Return the registered action names the close matches draw from."""
        raw = self._context.candidates
        return tuple(raw() if callable(raw) else raw)

    @cached_property
    def suggestions(self) -> tuple[str, ...]:
        """Return close matches for the name, computed on first access."""
        return tuple(difflib.get_close_matches(self.name, sorted(set(self.candidates))))

    @override
    def __str__(self) -> str:
        """Render the message the raise carried, or the one the context composes."""
        if self.args:
            return str(self.args[0])
        return self._composed

    @override
    def __reduce__(self) -> "tuple[Any, ...]":
        """Pickle the rendered message and drop the live candidates source."""
        state = {
            "_context": _LookupContext(
                self.name, self.page_path, (), self.registry_empty
            ),
            "suggestions": self.suggestions,
        }
        return (self.__class__, (str(self),), state)

    @cached_property
    def _composed(self) -> str:
        """Render the failure with scope, close matches, and registry state."""
        if self.page_path is None:
            searched = "Searched the shared registry (no page scope)."
        else:
            searched = (
                f"Searched page scope for {self.page_path} and the shared registry."
            )
        message = f"Unknown form action {self.name!r}. {searched}"
        if self.suggestions:
            rendered = ", ".join(repr(suggestion) for suggestion in self.suggestions)
            message = f"{message} Closest matches: {rendered}."
        if self.registry_empty:
            message = (
                f"{message} No form actions are registered. Check that the "
                "declaring module is imported. Autodiscover imports each "
                "app's forms.py when FORM_AUTODISCOVER is enabled."
            )
        return message


class UnstorableWizardValueError(ImproperlyConfigured):
    """Raised when a cleaned value does not fit the session wizard backend."""

    def __init__(self, value: object) -> None:
        """Store the value the JSON codec refuses."""
        self.value = value
        super().__init__(
            f"SessionFormWizardBackend cannot store {type(value).__name__} values. "
            "Configure CacheFormWizardBackend or a custom FormWizardBackend in "
            "FORM_WIZARD_BACKEND for cleaned_data that does not fit JSON."
        )


class UnregisteredComponentError(LookupError):
    """Raised when a `ComponentWidget` names a component nothing registered.

    The close matches come from what the caller saw, so the error needs no manager.
    """

    def __init__(
        self, name: str, anchor: "str | Path", visible: "Iterable[str]"
    ) -> None:
        """Store the unresolved name and the names visible from the anchor."""
        self.name = name
        self.anchor = anchor
        self._visible = tuple(visible)
        super().__init__()

    @cached_property
    def matches(self) -> tuple[str, ...]:
        """Return the closest visible names, computed on first access."""
        return tuple(difflib.get_close_matches(self.name, sorted(self._visible)))

    @override
    def __str__(self) -> str:
        """Render the failure with the anchor and the closest visible names."""
        message = (
            f"ComponentWidget references component {self.name!r} that is not "
            f"registered. Searched from {self.anchor}. Create {self.name}.djx in a "
            "_components directory visible from that path, or register the "
            "component through a components backend."
        )
        if self.matches:
            rendered = ", ".join(repr(match) for match in self.matches)
            message = f"{message} Closest matches: {rendered}."
        return message


__all__ = [
    "FormActionNotFoundError",
    "UnregisteredComponentError",
    "UnstorableWizardValueError",
]
