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
    """Raised when the backend an entry under one settings key names does not import.

    The entry is a mapping carrying no path of its own, so the key is all it names.
    """

    def __init__(self, setting: str, exc: object) -> None:
        """Store the settings key whose entry named the backend."""
        self.setting = setting
        super().__init__(
            f"NEXT_FRAMEWORK[{setting!r}] names a backend that cannot be "
            f"imported: {exc}"
        )


class SettingImportError(ImproperlyConfigured):
    """Raised when the class one dotted-path settings key names does not import."""

    def __init__(self, setting: str, dotted: str, exc: object) -> None:
        """Store the settings key and the dotted path it carries."""
        self.setting = setting
        self.dotted = dotted
        super().__init__(
            f"NEXT_FRAMEWORK[{setting!r}] {dotted!r} could not be imported: {exc}"
        )


class BackendNotSubclassError(ImproperlyConfigured):
    """Raised when the class a backend entry names is outside its family."""

    def __init__(self, dotted: str, base_name: str) -> None:
        """Store the named class and the family root it stands outside of."""
        self.dotted = dotted
        self.base_name = base_name
        super().__init__(f"Backend {dotted!r} is not a {base_name} subclass.")


class SettingNotSubclassError(ImproperlyConfigured):
    """Raised when the class one dotted-path key names stands outside its family."""

    def __init__(self, setting: str, dotted: str, base_name: str) -> None:
        """Store the settings key, the named class and the family root it misses."""
        self.setting = setting
        self.dotted = dotted
        self.base_name = base_name
        super().__init__(
            f"NEXT_FRAMEWORK[{setting!r}] {dotted!r} is not a {base_name} subclass."
        )


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
    "SettingImportError",
    "SettingNotSubclassError",
]
