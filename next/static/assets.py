"""Value objects and kind registry for static assets.

Depends on nothing that needs the app registry, so it imports early, and ships empty
so built-in kinds register through the same public API user code uses.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .errors import StaticAssetTraversalError


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
# own `inline_tag` differs is withheld, and `module` names no element at all.
_LOAD_INLINE_TAGS: Final[dict[str, str]] = {"link": "style", "script": "script"}


_VERSION_QUERY_KEY: Final = "v"


def _with_query_param(url: str, key: str, value: str) -> str:
    """Return `url` carrying exactly one `key` pair, set to `value`.

    Every other pair keeps the spelling it was authored with, while the named pair
    moves to the end, because only the version is rewritten and nothing reads order.
    """
    split = urlsplit(url)
    kept = [
        pair
        for pair in split.query.split("&")
        if pair and unquote(pair.partition("=")[0]) != key
    ]
    kept.append(f"{quote(key, safe='')}={quote(value)}")
    return urlunsplit(
        (split.scheme, split.netloc, split.path, "&".join(kept), split.fragment)
    )


def with_version(url: str, version: object) -> str:
    """Return the URL carrying a `v` query parameter naming the given version.

    An opaque URI such as `data:` or `blob:` owns no query, so a pair welded on
    would land inside its payload and it passes through untouched instead.
    """
    value = "" if version is None else str(version)
    if not value:
        return url
    split = urlsplit(url)
    if split.scheme and not split.netloc:
        return url
    return _with_query_param(url, _VERSION_QUERY_KEY, value)


def static_name(reference: str) -> str | None:
    """Return the staticfiles name a reference holds, or None when it is a ready URL.

    Read by the URL parser, so a scheme without `//` such as `data:` is no name, and
    the normalised path is what reaches a lookup rather than the reference verbatim.
    """
    split = urlsplit(reference)
    if split.scheme or split.netloc or split.query or split.fragment:
        return None
    if not split.path or split.path.startswith("/"):
        return None
    name = posixpath.normpath(split.path)
    if name == ".." or name.startswith("../"):
        raise StaticAssetTraversalError(reference)
    return name


class StaticNamespace:
    """Namespace constants used when building staticfiles URL paths.

    Public URLs have the form `/static/next/<logical_name>.<ext>`.
    """

    NEXT: Final = "next"


@dataclass(frozen=True, slots=True)
class StaticAsset:
    """Immutable record describing one asset reference.

    A URL form carries a non-empty `url` and an optional `source_path`, while a block
    form carries a pre-rendered `inline` body and leaves `url` empty.
    """

    url: str
    kind: str
    source_path: Path | None = None
    inline: str | None = None


class KindRegistry:
    """Mutable registry mapping asset kinds to extension, slot, and renderer.

    Ships empty, so built-in kinds use the same public `register` call user code does.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._extensions: dict[str, str] = {}
        self._slots: dict[str, str] = {}
        self._renderers: dict[str, str] = {}
        self._inline_tags: dict[str, str] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Return a counter every registration bumps, so a cached answer can tell."""
        return self._version

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

        `inline_tag` names the element wrapping a co-located inline body, and a repeated
        call with different parameters raises rather than masking a bug.
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
        self._version += 1

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

        An unregistered kind or custom renderer has no verb, so the wire omits the
        field. With `inline`, the verb also needs the kind's `inline_tag` to match.
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

        Without a wrapper registered the kind's inline bodies render verbatim.
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
