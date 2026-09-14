"""Exceptions the forms area raises for a lookup or a value it cannot serve."""

import difflib
from typing import TYPE_CHECKING, Any, override

from django.core.exceptions import ImproperlyConfigured


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path


class FormActionNotFoundError(LookupError):
    """No registered form action matches the requested name."""

    _suggestions: "tuple[str, ...] | None" = None

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
        # The manager probes backends by catching this, so raising stays cheap:
        # one packed attribute now, difflib and the message only when rendered.
        self._context: tuple[
            str, str | None, Callable[[], Iterable[str]] | Iterable[str], bool
        ] = (name, page_path, candidates, registry_empty)
        if message is None:
            super().__init__()
        else:
            super().__init__(message)

    @property
    def name(self) -> str:
        """Return the action name the failed lookup asked for."""
        return self._context[0]

    @property
    def page_path(self) -> str | None:
        """Return the page scope the lookup searched, when any."""
        return self._context[1]

    @property
    def registry_empty(self) -> bool:
        """Return True when no actions were registered at raise time."""
        return self._context[3]

    @property
    def candidates(self) -> tuple[str, ...]:
        """Return the registered action names the close matches draw from."""
        raw = self._context[2]
        return tuple(raw() if callable(raw) else raw)

    @property
    def suggestions(self) -> tuple[str, ...]:
        """Return close matches for the name, computed on first access."""
        if self._suggestions is None:
            self._suggestions = tuple(
                difflib.get_close_matches(self.name, sorted(set(self.candidates)))
            )
        return self._suggestions

    @override
    def __str__(self) -> str:
        """Render the message, composing and caching it on first access."""
        if not self.args:
            self.args = (self._compose(),)
        return str(self.args[0])

    @override
    def __reduce__(self) -> "tuple[Any, ...]":
        """Pickle the rendered message and drop the live candidates source."""
        state = {
            "_context": (self.name, self.page_path, (), self.registry_empty),
            "_suggestions": self.suggestions,
        }
        return (self.__class__, (str(self),), state)

    def _compose(self) -> str:
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


class UnregisteredComponentError(RuntimeError):
    """Raised when a `ComponentWidget` names a component nothing registered.

    The close matches come from what the caller saw, so the error needs no manager.
    """

    def __init__(
        self, name: str, anchor: "str | Path", visible: "Iterable[str]"
    ) -> None:
        """Store the unresolved name and render the closest visible matches."""
        self.name = name
        self.anchor = anchor
        self.matches = tuple(difflib.get_close_matches(name, sorted(visible)))
        message = (
            f"ComponentWidget references component {name!r} that is not "
            f"registered. Searched from {anchor}. Create {name}.djx in a "
            "_components directory visible from that path, or register the "
            "component through a components backend."
        )
        if self.matches:
            rendered = ", ".join(repr(match) for match in self.matches)
            message = f"{message} Closest matches: {rendered}."
        super().__init__(message)


__all__ = [
    "FormActionNotFoundError",
    "UnregisteredComponentError",
    "UnstorableWizardValueError",
]
