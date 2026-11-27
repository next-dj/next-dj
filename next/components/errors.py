"""Exceptions the components area raises for a `component.py` it cannot load."""

from pathlib import Path


class ComponentModuleImportError(Exception):
    """A `component.py` body raised while importing.

    The original exception is the `__cause__`, and `file_path` names the failing file.
    """

    def __init__(self, file_path: Path) -> None:
        """Compose the message from the failing path."""
        super().__init__(f"{file_path} failed to import")
        self.file_path = file_path


class ComponentContextConflictError(ValueError):
    """A keyless `@component.context` returned a name the render already holds.

    A prop of the calling tag, a reserved render key and a slot name win over the merge.
    """

    def __init__(self, component: str, function: str, keys: tuple[str, ...]) -> None:
        """Compose the message from the component, the function and the keys."""
        names = ", ".join(repr(key) for key in keys)
        super().__init__(
            f"Component {component!r} context returns {names}, held by a prop of the "
            "calling tag, a reserved render key or a slot, which the keyless merge of "
            f"{function!r} never overwrites. Register the value under "
            "@component.context(key) to set it, or drop it from the returned dict."
        )
        self.component = component
        self.function = function
        self.keys = keys


__all__ = ["ComponentContextConflictError", "ComponentModuleImportError"]
