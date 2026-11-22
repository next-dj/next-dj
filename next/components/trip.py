"""What a client-trip component call hands the layer that mints its token."""

from __future__ import annotations

import typing
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Annotated, Final, Protocol, runtime_checkable

from next.caches import BoundedCache
from next.defer import DeferMode
from next.deps.introspect import cached_signature, cached_type_hints
from next.utils import app_holding, resolve_base_dir

from .context import component
from .defer import defer_declarations


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from next.defer import BoundaryFailure

    from .info import ComponentInfo


CLIENT_MODES: Final = frozenset({DeferMode.LOAD, DeferMode.VISIBLE, DeferMode.MANUAL})
"""The modes whose boundary the browser loads on a later request of its own."""

type Overrides = tuple[tuple[str, object], ...]

_types: BoundedCache[Path, tuple[tuple[int, int], Mapping[str, object]]] = (
    BoundedCache()
)
_anchored: BoundedCache[tuple[Path, Path], str | None] = BoundedCache()


class _Miss(Enum):
    """The answer `_anchored` holds for no pair of paths, `None` being an answer."""

    TOKEN = auto()


def client_mode(mode: DeferMode | None) -> bool:
    """Whether `mode` delivers its boundary on a later request of the browser."""
    return mode in CLIENT_MODES


def declared_props(info: ComponentInfo) -> tuple[str, ...]:
    """Return the props the `component.py` of `info` declares, in declaration order."""
    options = defer_declarations.options(info.module_path)
    return () if options is None else options.props


def addressable(info: ComponentInfo) -> bool:
    """Whether the `component.py` of `info` names its props, so a token reloads it."""
    return defer_declarations.addressable(info.module_path)


def token_overrides(info: ComponentInfo) -> Overrides:
    """Return the `tokens={...}` overrides the `component.py` of `info` declares."""
    options = defer_declarations.options(info.module_path)
    return () if options is None else options.tokens


def component_scope(info: ComponentInfo) -> str:
    """Return the directory of `info` as the same text on every host of a deployment.

    Two components of one name in different directories never share a token.
    """
    source = info.module_path or info.template_path
    if source is None:
        return info.scope_relative
    directory = source.parent
    held = app_holding(directory)
    if held is not None:
        return f"{held[0]}:{held[1]}"
    base = resolve_base_dir()
    anchored = None if base is None else _under(directory, base)
    return info.scope_relative if anchored is None else anchored


def _under(directory: Path, base: Path) -> str | None:
    """Return `directory` relative to `base` once symlinks resolve, `None` outside it.

    Resolving reads the disk, so the answer of one pair of paths is kept.
    """
    key = (directory, base)
    held = _anchored.get(key, _Miss.TOKEN)
    if held is not _Miss.TOKEN:
        return held
    real = directory.resolve()
    root = base.resolve()
    found = real.relative_to(root).as_posix() if real.is_relative_to(root) else None
    _anchored[key] = found
    return found


def _bare(annotation: object) -> object:
    """Return `annotation` without the `Annotated` extras a provider marker adds."""
    if typing.get_origin(annotation) is Annotated:
        return typing.get_args(annotation)[0]
    return annotation


def _read_types(module_path: Path, names: tuple[str, ...]) -> dict[str, object]:
    """Return the annotation of each declared prop a context function takes."""
    found: dict[str, object] = {}
    for entry in component.get_functions(module_path):
        hints = cached_type_hints(entry.func)
        for name in cached_signature(entry.func).parameters:
            if name in names and name not in found and name in hints:
                found[name] = _bare(hints[name])
    return found


def prop_types(info: ComponentInfo) -> Mapping[str, object]:
    """Return the type of each declared prop, the annotation its context reads.

    A prop no context function takes travels as `str`, kept until a registry changes.
    """
    module_path = info.module_path
    if module_path is None:
        return {}
    version = (component.registry.version, defer_declarations.version)
    held = _types.get(module_path)
    if held is not None and held[0] == version:
        return held[1]
    types = _read_types(module_path, declared_props(info))
    _types[module_path] = (version, types)
    return types


@dataclass(frozen=True, slots=True)
class TripInput:
    """One component call a token is minted for, after its access rule passed.

    `props` holds the declared props alone, `perms` the enclosing `has_perm` names.
    """

    info: ComponentInfo
    mode: DeferMode
    props: Mapping[str, object]
    perms: tuple[str, ...] = ()


@runtime_checkable
class TripSource(Protocol):
    """A deferred call renderer that can name the input of a boundary token."""

    def trip_input(self) -> TripInput | None:
        """Return the input of a client-trip shell, `None` for another call."""

    def retry_input(self, failure: BoundaryFailure) -> TripInput | None:
        """Return the input a retry token of `failure` is minted from, if any."""


__all__ = [
    "CLIENT_MODES",
    "Overrides",
    "TripInput",
    "TripSource",
    "addressable",
    "client_mode",
    "component_scope",
    "declared_props",
    "prop_types",
    "token_overrides",
]
