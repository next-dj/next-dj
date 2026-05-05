"""Value objects and kind registry for static assets.

This module holds the leaf building blocks of the static subsystem. It
defines a frozen value object for a single asset reference and a mutable
registry that maps asset kinds to file extensions, placeholder slots,
and renderer method names. The module has no internal dependencies and
is safe to import before the Django app registry is ready.

The registry ships empty. Built-in kinds such as `css` and `js` are
registered by the framework bootstrap layer through the same public
`register` call that user code uses to teach the framework about new
file types like `jsx` or `wasm`. Core code never special-cases any
particular kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final


if TYPE_CHECKING:
    from pathlib import Path


class StaticNamespace:
    """Namespace constants used when building staticfiles URL paths.

    The `NEXT` constant is the top-level directory under which the
    framework publishes co-located assets inside the Django staticfiles
    tree. Public URLs have the form `/static/next/<logical_name>.<ext>`.
    """

    NEXT: Final = "next"


@dataclass(frozen=True, slots=True)
class StaticAsset:
    """Immutable record describing one asset reference.

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
    """Mutable registry mapping asset kinds to extension, slot, and renderer.

    The registry ships empty. Bootstrap code registers built-in kinds
    such as `css` and `js` through `register`, and user code registers
    additional kinds the same way during `AppConfig.ready`.

    Each registration carries three pieces of information.

    The `extension` field is the file suffix associated with the kind.
    Discovery walks every registered kind and looks for files matching
    `{stem}{extension}` next to each template, layout, or component.

    The `slot` field is the name of the placeholder slot that buckets
    this asset at render time. Slots are owned by a sibling
    `PlaceholderRegistry` and identify where the rendered tags land in
    the final HTML.

    The `renderer` field is the method name that the configured static
    backend exposes for rendering asset URLs of this kind. The manager
    looks the method up on the active backend with `getattr` per asset.

    Example usage during framework bootstrap.

    Example::

        default_kinds.register(
            "css",
            extension=".css",
            slot="styles",
            renderer="render_link_tag",
        )
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._extensions: dict[str, str] = {}
        self._slots: dict[str, str] = {}
        self._renderers: dict[str, str] = {}

    def register(
        self,
        kind: str,
        *,
        extension: str,
        slot: str,
        renderer: str,
    ) -> None:
        """Register an asset kind and its dispatch metadata.

        The `kind` argument must be a non-empty Python identifier. The
        `extension` argument must begin with a dot. The `slot` and
        `renderer` arguments must be non-empty strings. Any other input
        raises `ValueError`. A repeated call with identical parameters
        is idempotent. A repeated call with different parameters raises
        `ValueError` so silent re-registrations cannot mask bugs.
        """
        if not kind or not kind.isidentifier():
            msg = f"Invalid kind {kind!r}: must be a non-empty identifier"
            raise ValueError(msg)
        if not extension.startswith("."):
            msg = f"Extension {extension!r} must start with '.'"
            raise ValueError(msg)
        if not slot:
            msg = "Slot name must be a non-empty string"
            raise ValueError(msg)
        if not renderer:
            msg = "Renderer method name must be a non-empty string"
            raise ValueError(msg)
        existing = self._extensions.get(kind)
        if existing is not None:
            current = (existing, self._slots[kind], self._renderers[kind])
            incoming = (extension, slot, renderer)
            if current == incoming:
                return
            msg = (
                f"Kind {kind!r} is already registered with "
                f"extension={existing!r}, slot={self._slots[kind]!r}, "
                f"renderer={self._renderers[kind]!r}. Cannot re-register "
                f"with extension={extension!r}, slot={slot!r}, renderer={renderer!r}."
            )
            raise ValueError(msg)
        self._extensions[kind] = extension
        self._slots[kind] = slot
        self._renderers[kind] = renderer

    def extension(self, kind: str) -> str:
        """Return the file extension registered for the given kind.

        Raises `KeyError` when the kind has not been registered.
        """
        if kind not in self._extensions:
            msg = f"Unsupported asset kind: {kind!r}"
            raise KeyError(msg)
        return self._extensions[kind]

    def slot(self, kind: str) -> str:
        """Return the placeholder slot name registered for the given kind."""
        if kind not in self._slots:
            msg = f"Unsupported asset kind: {kind!r}"
            raise KeyError(msg)
        return self._slots[kind]

    def renderer(self, kind: str) -> str:
        """Return the backend method name registered for the given kind."""
        if kind not in self._renderers:
            msg = f"Unsupported asset kind: {kind!r}"
            raise KeyError(msg)
        return self._renderers[kind]

    def kind_for_extension(self, extension: str) -> str | None:
        """Return the kind registered for the given extension or None."""
        for kind, ext in self._extensions.items():
            if ext == extension:
                return kind
        return None

    def kinds(self) -> tuple[str, ...]:
        """Return all registered kinds in registration order."""
        return tuple(self._extensions)

    def __contains__(self, kind: object) -> bool:
        """Return True when the given value is a registered asset kind."""
        return isinstance(kind, str) and kind in self._extensions


default_kinds: KindRegistry = KindRegistry()
