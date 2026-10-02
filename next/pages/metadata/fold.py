"""The fold of the metadata segment chain, one merge strategy per dataclass field.

The strategies live on the fields in `markers.py`, so one walk serves every block.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from operator import attrgetter
from typing import Any, Final, cast

from .dicts import Text
from .ld import Node, raw_id
from .markers import MERGE_KEY, Crumb, Merge, Metadata, Segment
from .titles import apply_title_template


type Strategies = tuple[tuple[str, Merge], ...]
type Pairs = tuple[tuple[str, Text], ...]
type Objects = tuple[Node | Mapping[str, object], ...]

_OWN_FOLDS: Final = frozenset({"title", "breadcrumbs"})


_STRATEGIES: dict[type, Strategies] = {}


def strategies(cls: type) -> Strategies:
    """Return each field of a metadata dataclass with the strategy that folds it.

    Read once per class, so the fold walks a tuple, and empty for a class with none.
    """
    held = _STRATEGIES.get(cls)
    if held is None:
        held = _STRATEGIES[cls] = (
            tuple(
                (item.name, item.metadata.get(MERGE_KEY, Merge.REPLACE))
                for item in fields(cls)
            )
            if is_dataclass(cls)
            else ()
        )
    return held


_FIELDS: Final[Strategies] = tuple(
    (name, strategy)
    for name, strategy in strategies(Metadata)
    if name not in _OWN_FOLDS
)
_NAMES: Final = tuple(name for name, _ in _FIELDS)
_VALUES: Final[Callable[[Metadata], tuple[object, ...]]] = attrgetter(*_NAMES)


@dataclass(frozen=True, slots=True)
class FoldState:
    """The chain folded so far, its title still waiting for the final site name.

    `wrap` is the template the title text sits under and `template` the one in effect.
    """

    values: tuple[object, ...]
    title: Text | None = None
    wrap: Text | None = None
    template: Text | None = None
    crumbs: tuple[Crumb, ...] = ()


EMPTY_STATE: Final = FoldState(_VALUES(Metadata()))
"""The state no segment has been folded into yet."""


def _unset(value: object) -> bool:
    """Whether a field was left at its default, without comparing a Promise."""
    return value is None or (isinstance(value, tuple) and not value)


def _reset_names(path: str, replaced: frozenset[str]) -> frozenset[str]:
    prefix = f"{path}."
    return frozenset(key[len(prefix) :] for key in replaced if key.startswith(prefix))


def _by_name(older: Pairs, newer: Pairs, dropped: frozenset[str]) -> Pairs:
    """Replace every value of a name the nearer segment names, keeping older order."""
    if not newer and not dropped:
        return older
    fresh: dict[str, list[tuple[str, Text]]] = {}
    for pair in newer:
        fresh.setdefault(pair[0], []).append(pair)
    folded: list[tuple[str, Text]] = []
    placed: set[str] = set()
    for pair in older:
        name = pair[0]
        if name in fresh:
            if name not in placed:
                folded.extend(fresh[name])
                placed.add(name)
        elif name not in dropped:
            folded.append(pair)
    folded.extend(pair for pair in newer if pair[0] not in placed)
    return tuple(folded)


def _by_id(older: Objects, newer: Objects) -> Objects:
    """Replace an object in place by its raw `@id`, appending one without an id."""
    folded = list(older)
    index = {
        ident: position
        for position, item in enumerate(folded)
        if (ident := raw_id(item)) is not None
    }
    for item in newer:
        ident = raw_id(item)
        if ident is not None:
            if ident in index:
                folded[index[ident]] = item
                continue
            index[ident] = len(folded)
        folded.append(item)
    return tuple(folded)


def _merge_values(
    strategies_of: Strategies,
    older: Iterable[object],
    newer: Iterable[object],
    prefix: str,
    replaced: frozenset[str],
) -> tuple[object, ...]:
    """Merge two values field by field, the plain nearest-wins inline.

    `prefix` spells the path of the block, `""` at the top and `"og."` below it.
    """
    return tuple(
        (old if _unset(new) else new)
        if strategy is Merge.REPLACE and not replaced
        else merge_field(strategy, old, new, path=f"{prefix}{name}", replaced=replaced)
        for (name, strategy), old, new in zip(strategies_of, older, newer, strict=True)
    )


def _deep(older: object, newer: object, path: str, replaced: frozenset[str]) -> object:
    """Merge two blocks of one class field by field, else let the nearer one win."""
    cls = type(newer)
    held = strategies(cls) if type(older) is cls else ()
    if not held:
        return newer
    names = [name for name, _strategy in held]
    merged = _merge_values(
        held,
        (getattr(older, name) for name in names),
        (getattr(newer, name) for name in names),
        f"{path}.",
        replaced,
    )
    return cls(**dict(zip(names, merged, strict=True)))


def merge_field(
    strategy: Merge,
    older: object,
    newer: object,
    *,
    path: str,
    replaced: frozenset[str] = frozenset(),
) -> object:
    """Fold the value one segment declares under `path` over the inherited one.

    A path in `replaced` takes the nearer value whole, unset or not.
    """
    if replaced and path in replaced:
        return newer
    if strategy is Merge.BY_NAME:
        dropped = _reset_names(path, replaced) if replaced else frozenset()
        return _by_name(cast("Pairs", older), cast("Pairs", newer), dropped)
    if _unset(newer):
        return older
    if strategy is Merge.DEEP:
        return _deep(older, newer, path, replaced)
    if strategy is Merge.BY_ID:
        return _by_id(cast("Objects", older), cast("Objects", newer))
    return newer


def _fold_title(state: FoldState, segment: Segment) -> tuple[Text | None, ...]:
    """Return the title, its wrap and the template in effect after `segment`."""
    if "title" in segment.replaced:
        title = wrap = template = None
    else:
        title, wrap, template = state.title, state.wrap, state.template
    spec = segment.title
    if spec is None:
        return title, wrap, template
    if spec.absolute is not None:
        title, wrap = spec.absolute, None
    elif spec.text is not None:
        title, wrap = spec.text, template
    elif spec.default is not None:
        title, wrap = spec.default, None
    if spec.template is not None:
        template = spec.template
    return title, wrap, template


def _own_label(segment: Segment) -> Text | None:
    """Return the crumb label of a segment, its own untemplated title as a fallback."""
    label = segment.breadcrumb
    spec = segment.title
    if label is None and spec is not None:
        label = spec.text if spec.absolute is None else spec.absolute
    return None if label is False else label


def _fold_crumbs(crumbs: tuple[Crumb, ...], segment: Segment) -> tuple[Crumb, ...]:
    """Read the breadcrumb of one segment on its own, never inherited."""
    trail = segment.trail
    label = None if trail is None else _own_label(segment)
    if trail is None or label is None:
        return crumbs
    return (*crumbs, Crumb(label, trail))


def fold_segment(state: FoldState, segment: Segment) -> FoldState:
    """Fold one segment over the state its ancestors left."""
    values = _merge_values(
        _FIELDS, state.values, _VALUES(segment.metadata), "", segment.replaced
    )
    title, wrap, template = _fold_title(state, segment)
    crumbs = _fold_crumbs(state.crumbs, segment)
    return FoldState(values, title, wrap, template, crumbs)


def fold_segments(
    segments: Iterable[Segment], state: FoldState = EMPTY_STATE
) -> FoldState:
    """Fold `segments` root to leaf over `state`."""
    for segment in segments:
        state = fold_segment(state, segment)
    return state


def finish(state: FoldState) -> Metadata:
    """Turn a state into the folded value, its template reading the final site name."""
    values = cast("dict[str, Any]", dict(zip(_NAMES, state.values, strict=True)))
    title = state.title
    if title is not None and state.wrap is not None:
        title = apply_title_template(state.wrap, title, site_name=values["site_name"])
    return Metadata(title=title, breadcrumbs=state.crumbs, **values)


def fold_metadata(segments: Iterable[Segment]) -> Metadata:
    """Fold the chain from root to leaf, each field under its merge strategy."""
    return finish(fold_segments(segments))


def merge_segments(base: Segment, over: Segment) -> Segment:
    """Lay `over` onto `base` as one segment, the title of `over` taking its place.

    A key `over` replaces drops what `base` declares, and still drops the inherited.
    """
    replaced = over.replaced
    merged = _merge_values(
        _FIELDS, _VALUES(base.metadata), _VALUES(over.metadata), "", replaced
    )
    values = cast("dict[str, Any]", dict(zip(_NAMES, merged, strict=True)))
    title = over.title
    if title is None and "title" not in replaced:
        title = base.title
    return Segment(
        over.source,
        Metadata(**values),
        title=title,
        breadcrumb=base.breadcrumb if over.breadcrumb is None else over.breadcrumb,
        trail=base.trail if over.trail is None else over.trail,
        replaced=base.replaced | replaced,
    )


def _forget_below(origins: dict[str, str], path: str) -> None:
    prefix = f"{path}."
    for key in [key for key in origins if key == path or key.startswith(prefix)]:
        del origins[key]


def _trace(
    strategy: Merge, value: object, path: str, source: str, origins: dict[str, str]
) -> None:
    """Record `source` as the origin of every key `value` settles under `path`."""
    if _unset(value):
        return
    inner_strategies = strategies(type(value)) if strategy is Merge.DEEP else ()
    if inner_strategies:
        origins.pop(path, None)
        for name, inner in inner_strategies:
            _trace(inner, getattr(value, name), f"{path}.{name}", source, origins)
        return
    if strategy is Merge.BY_NAME:
        for name, _text in cast("Pairs", value):
            origins[f"{path}.{name}"] = source
        return
    if strategy is Merge.BY_ID:
        opening = f"{path}["
        for item in cast("Objects", value):
            ident = raw_id(item)
            if ident is None:
                ident = str(
                    sum(
                        key.startswith(opening) and key[len(opening) : -1].isdigit()
                        for key in origins
                    )
                )
            origins[f"{opening}{ident}]"] = source
        return
    _forget_below(origins, path)
    origins[path] = source


def trace_origins(segments: Iterable[Segment]) -> dict[str, str]:
    """Map every key path the chain settles to the source of the segment settling it.

    A reset key names the segment that dropped it, since that is where to look.
    """
    origins: dict[str, str] = {}
    for segment in segments:
        source = segment.source
        for path in sorted(segment.replaced):
            _forget_below(origins, path)
            origins[path] = source
        if segment.title is not None:
            origins["title"] = source
        meta = segment.metadata
        for name, strategy in _FIELDS:
            _trace(strategy, getattr(meta, name), name, source, origins)
    return origins


__all__ = [
    "EMPTY_STATE",
    "FoldState",
    "finish",
    "fold_metadata",
    "fold_segment",
    "fold_segments",
    "merge_segments",
    "trace_origins",
]
