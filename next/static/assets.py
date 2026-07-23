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


# The client insertion verb each built-in renderer stands for. The registry owns
# the table because it is the only place that knows which renderer a kind uses.
_RENDERER_LOADS: Final[dict[str, str]] = {
    "render_link_tag": "link",
    "render_script_tag": "script",
    "render_module_tag": "module",
}

# The element the runtime builds around an inline body for each verb. A kind whose
# own `inline_tag` differs would render that body into another element on a full
# page render, so the verb is withheld instead of letting the two renders disagree.
# The `module` verb builds a typed script no `inline_tag` can name, hence its absence.
_LOAD_INLINE_TAGS: Final[dict[str, str]] = {"link": "style", "script": "script"}


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

    A registration binds the file suffix discovery looks for, the
    placeholder slot the rendered tags land in, and the backend method
    that renders a URL of the kind. The registry ships empty so built-in
    kinds go through the same public `register` call user code uses.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._extensions: dict[str, str] = {}
        self._slots: dict[str, str] = {}
        self._renderers: dict[str, str] = {}
        self._inline_tags: dict[str, str] = {}

    def register(
        self,
        kind: str,
        *,
        extension: str,
        slot: str,
        renderer: str,
        inline_tag: str | None = None,
    ) -> None:
        """Register an asset kind and its dispatch metadata.

        The `kind` argument must be a non-empty Python identifier. The
        `extension` argument must begin with a dot. The `slot` and
        `renderer` arguments must be non-empty strings. Any other input
        raises `ValueError`. The optional `inline_tag` names the HTML
        element that wraps a co-located inline body for this kind, for
        example `"style"` or `"script"`. When omitted, inline bodies of
        this kind render verbatim. A repeated call with identical
        parameters is idempotent. A repeated call with different
        parameters raises `ValueError` so silent re-registrations cannot
        mask bugs.
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
            current = (
                existing,
                self._slots[kind],
                self._renderers[kind],
                self._inline_tags.get(kind),
            )
            incoming = (extension, slot, renderer, inline_tag)
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
        if inline_tag is not None:
            self._inline_tags[kind] = inline_tag

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

    def load(self, kind: str, *, inline: bool = False) -> str | None:
        """Return the client insertion verb for the kind, or None when it has none.

        A kind that is not registered, or one whose renderer is a custom
        backend method, has no verb the runtime can act on, so the wire
        omits the field rather than guessing. With `inline` the verb also
        requires the kind's `inline_tag` to be the element the runtime
        builds, so a body rendered verbatim on a full page render is never
        wrapped and executed by a patch instead.
        """
        renderer = self._renderers.get(kind)
        if renderer is None:
            return None
        load = _RENDERER_LOADS.get(renderer)
        if load is None or not inline:
            return load
        expected = _LOAD_INLINE_TAGS.get(load)
        if expected is None or expected != self._inline_tags.get(kind):
            return None
        return load

    def inline_tag(self, kind: str) -> str | None:
        """Return the inline wrapper element for the kind or None.

        A `None` result means inline bodies of this kind render
        verbatim, preserving backward compatibility for custom kinds
        registered without an inline wrapper.
        """
        return self._inline_tags.get(kind)

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
