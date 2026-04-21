"""Value objects and kind registry for static assets.

This module holds the leaf building blocks of the static subsystem. It
defines a frozen value object for a single asset reference and a mutable
registry that maps asset kinds to their file extensions. The module has
no internal dependencies and is safe to import before the Django app
registry is ready.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final


if TYPE_CHECKING:
    from pathlib import Path


_KIND_CSS: Final = "css"
_KIND_JS: Final = "js"


class StaticNamespace:
    """Namespace constants used when building staticfiles URL paths.

    The `NEXT` constant is the top-level directory under which the
    framework publishes co-located assets inside the Django staticfiles
    tree. Public URLs have the form `/static/next/<logical_name>.<ext>`.
    """

    NEXT: Final = "next"


@dataclass(frozen=True, slots=True)
class StaticAsset:
    """Immutable record describing one CSS or JS asset reference.

    The collector populates instances of this class during page render.
    A URL form carries a non-empty `url` and an optional `source_path`
    pointing at the co-located file on disk. A block form carries a
    pre-rendered `inline` body and leaves `url` empty. The `kind` field
    must match a kind registered in the active `KindRegistry`.
    """

    url: str
    kind: str
    source_path: Path | None = None
    inline: str | None = None


class KindRegistry:
    """Mutable registry mapping asset kinds to file extensions.

    The default registry ships with `css` mapped to `.css` and `js`
    mapped to `.js`. Users may register additional kinds during
    `AppConfig.ready` to teach discovery, backends, and collectors how
    to handle new file types.

    Example usage inside an `AppConfig.ready` method.

    Example::

        from next.static import default_kinds


        class MyAppConfig(AppConfig):
            def ready(self) -> None:
                default_kinds.register("wasm", ".wasm")
    """

    def __init__(self) -> None:
        """Seed the registry with the built-in `css` and `js` kinds."""
        self._map: dict[str, str] = {_KIND_CSS: ".css", _KIND_JS: ".js"}

    def register(self, kind: str, extension: str) -> None:
        """Register an asset kind with its file extension.

        The `kind` argument must be a non-empty Python identifier. The
        `extension` argument must begin with a dot. Passing any other
        value raises `ValueError`.
        """
        if not kind or not kind.isidentifier():
            msg = f"Invalid kind {kind!r}: must be a non-empty identifier"
            raise ValueError(msg)
        if not extension.startswith("."):
            msg = f"Extension {extension!r} must start with '.'"
            raise ValueError(msg)
        self._map[kind] = extension

    def extension(self, kind: str) -> str:
        """Return the file extension registered for the given kind.

        Raises `KeyError` when the kind has not been registered.
        """
        if kind not in self._map:
            msg = f"Unsupported asset kind: {kind!r}"
            raise KeyError(msg)
        return self._map[kind]

    def kind_for_extension(self, extension: str) -> str | None:
        """Return the kind associated with the given extension.

        Returns `None` when no registered kind uses that extension.
        """
        for kind, ext in self._map.items():
            if ext == extension:
                return kind
        return None

    def kinds(self) -> tuple[str, ...]:
        """Return all registered kinds in registration order."""
        return tuple(self._map)

    def __contains__(self, kind: object) -> bool:
        """Return True when the given value is a registered asset kind."""
        return isinstance(kind, str) and kind in self._map


default_kinds: KindRegistry = KindRegistry()
