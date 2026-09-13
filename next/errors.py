"""Exceptions a misconfigured project raises across every area."""

from django.core.exceptions import ImproperlyConfigured


class InvalidDirsError(ImproperlyConfigured):
    """Raised when a ``DIRS`` value is no sequence of trees.

    A string is refused with the other scalars, because iterating one hands the
    split its characters and a ``/`` among them would name the filesystem root.
    """

    def __init__(self, entries: object) -> None:
        """Store the offending value and build a readable message."""
        self.entries = entries
        super().__init__(
            f"A backend entry takes a sequence of trees under DIRS, got {entries!r}."
        )


class BackendPathError(ImproperlyConfigured):
    """Raised when a backend entry names its class by anything but a dotted path."""

    def __init__(self, root_name: str, config: object) -> None:
        """Store the family root and the entry that names no path."""
        self.root_name = root_name
        self.config = config
        super().__init__(
            f"A {root_name} entry names its backend by a dotted path "
            f"under BACKEND, got {config!r}."
        )


class BackendImportError(ImproperlyConfigured):
    """Raised when the class a settings entry names does not import.

    The dotted path is named when the setting carries one path alone, and left
    out where the entry is a whole backend mapping.
    """

    def __init__(self, setting: str, exc: object, dotted: str | None = None) -> None:
        """Store the setting that named the class and the import failure."""
        self.setting = setting
        self.dotted = dotted
        subject = (
            "names a backend that cannot be imported"
            if dotted is None
            else f"{dotted!r} could not be imported"
        )
        super().__init__(f"NEXT_FRAMEWORK[{setting!r}] {subject}: {exc}")


class BackendNotSubclassError(ImproperlyConfigured):
    """Raised when the class a settings entry names is outside its family.

    The setting is named where one dotted path carries the whole choice, so a
    project reads which key it has to fix.
    """

    def __init__(self, dotted: str, base_name: str, setting: str | None = None) -> None:
        """Store the named class and the family root it stands outside of."""
        self.dotted = dotted
        self.base_name = base_name
        self.setting = setting
        subject = (
            f"Backend {dotted!r}"
            if setting is None
            else f"NEXT_FRAMEWORK[{setting!r}] {dotted!r}"
        )
        super().__init__(f"{subject} is not a {base_name} subclass.")


class AbstractBackendError(ImproperlyConfigured):
    """Raised when a settings entry names the abstract root of a backend family."""

    def __init__(self, dotted: str) -> None:
        """Store the abstract class the entry named."""
        self.dotted = dotted
        super().__init__(
            f"Backend {dotted!r} is abstract, so it names no usable backend."
        )


__all__ = [
    "AbstractBackendError",
    "BackendImportError",
    "BackendNotSubclassError",
    "BackendPathError",
    "InvalidDirsError",
]
