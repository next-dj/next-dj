"""The strict normaliser that turns one raw metadata mapping into a `Segment`.

The accepted shapes are compiled once at import from the annotations in `dicts.py`.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date
from functools import partial
from types import UnionType
from typing import (
    Any,
    Final,
    Literal,
    TypeAliasType,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from next.utils import is_int

from .dicts import (
    AlternatesDict,
    ArticleDict,
    BookDict,
    FeedDict,
    GooglebotDict,
    IconDict,
    IconsDict,
    LinkDict,
    MetadataDict,
    OpenGraphAudioDict,
    OpenGraphDict,
    OpenGraphImageDict,
    OpenGraphVideoDict,
    OtherIconDict,
    ProfileDict,
    RobotsDict,
    SiteMetadataDict,
    SiteTitleDict,
    Text,
    ThemeColorDict,
    TitleDict,
    TwitterDict,
    TwitterImageDict,
    TwitterPlayerDict,
    Url,
    VerificationDict,
    ViewportDict,
)
from .markers import (
    Article,
    Book,
    Feed,
    Icon,
    Metadata,
    OpenGraph,
    OpenGraphAudio,
    OpenGraphImage,
    OpenGraphVideo,
    Profile,
    Replace,
    Robots,
    Segment,
    ThemeColor,
    TitleSpec,
    Twitter,
    TwitterImage,
    TwitterPlayer,
    Verification,
    Viewport,
)
from .shapes import (
    ICON_RELS,
    LINK_ATTRS,
    X_DEFAULT,
    _AlternatesBlock,
    _Block,
    _Canonical,
    _child,
    _Either,
    _icon,
    _icons,
    _is_bool,
    _is_choice,
    _is_date,
    _is_number,
    _is_str,
    _is_text,
    _Items,
    _JsonLd,
    _keyword,
    _Kind,
    _Languages,
    _Leaf,
    _link,
    _Named,
    _one,
    _Then,
    _Url,
    _Walk,
)


_LEAVES: Final[Mapping[object, _Kind]] = {
    str: _Leaf("a string", _is_str),
    Text: _Leaf("text", _is_text),
    Url: _Url(),
    bool: _Leaf("a bool", _is_bool),
    int: _Leaf("an int", is_int),
    float: _Leaf("a number", _is_number),
    date: _Leaf("a datetime, a date", _is_date),
}
"""The shape of each scalar annotation, the aliases `Text` and `Url` among them."""

_BUILDS: Final[Mapping[object, Callable[..., object]]] = {
    MetadataDict: dict,
    SiteMetadataDict: dict,
    TitleDict: TitleSpec,
    SiteTitleDict: TitleSpec,
    RobotsDict: Robots,
    GooglebotDict: Robots,
    OpenGraphDict: OpenGraph,
    OpenGraphImageDict: OpenGraphImage,
    OpenGraphVideoDict: OpenGraphVideo,
    OpenGraphAudioDict: OpenGraphAudio,
    ArticleDict: Article,
    ProfileDict: Profile,
    BookDict: Book,
    TwitterDict: Twitter,
    TwitterImageDict: TwitterImage,
    TwitterPlayerDict: TwitterPlayer,
    FeedDict: Feed,
    AlternatesDict: dict,
    VerificationDict: Verification,
    IconDict: _icon,
    OtherIconDict: Icon,
    IconsDict: _icons,
    LinkDict: _link,
    ViewportDict: Viewport,
    ThemeColorDict: ThemeColor,
}
"""The value each input dict builds, the page dicts a plain dict `_segment` splits."""

_SHORTHANDS: Final[Mapping[object, str]] = {
    TitleDict: "text",
    OpenGraphImageDict: "url",
    OpenGraphVideoDict: "url",
    OpenGraphAudioDict: "url",
    TwitterImageDict: "url",
    IconDict: "url",
    ThemeColorDict: "color",
}
"""The key a bare string given in place of one of these dicts sets."""

_PATH_KINDS: Final[Mapping[str, _Kind]] = {
    "canonical": _Canonical(),
    "jsonld": _JsonLd(),
    "alternates.languages": _Languages(),
}
"""The keys whose shape their annotation cannot say, by their path."""

_BARE: Final = (str, Text, Url)
"""The scalars a shorthand stands in for."""


def _options(hint: object) -> tuple[object, ...]:
    """Return what a hint admits, unions and aliases flattened and `Replace` dropped."""
    if hint in _LEAVES:
        return (hint,)
    if isinstance(hint, TypeAliasType):
        return _options(hint.__value__)
    origin = get_origin(hint)
    if origin is Union or origin is UnionType:
        return tuple(option for arg in get_args(hint) for option in _options(arg))
    if origin is Replace:
        return ()
    return (hint,)


def _kind(hint: object, path: str) -> _Kind:
    """Compile the shape of one annotated key.

    A bare value given where a sequence of such values is expected is wrapped in one.
    """
    override = _PATH_KINDS.get(path)
    if override is not None:
        return override
    options = _options(hint)
    many = next((option for option in options if get_origin(option) is Sequence), None)
    items = () if many is None else _options(get_args(many)[0])
    short = next(
        (option for option in (*options, *items) if option in _SHORTHANDS), None
    )
    kinds: list[_Kind] = []
    for option in options:
        stands = short if short is not None and option in _BARE else option
        kind = _shape(option, path) if stands is option else _shorthand(stands)
        kinds.append(_Then(kind, _one) if stands in items else kind)
    return kinds[0] if len(kinds) == 1 else _Either(tuple(kinds))


def _shorthand(block: object) -> _Kind:
    """Return the shape of a bare value that sets one key of `block`.

    A key the dict does not declare, the text of a title, takes text.
    """
    key = _SHORTHANDS[block]
    kind = _LEAVES[get_type_hints(block).get(key, Text)]
    return _Then(kind, partial(_keyword, _BUILDS[block], key))


def _shape(option: object, path: str) -> _Kind:
    """Compile one option of a union, a scalar, a sequence, a mapping or a dict."""
    leaf = _LEAVES.get(option)
    if leaf is not None:
        return leaf
    origin = get_origin(option)
    if origin is Literal:
        choices = get_args(option)
        return _Leaf(" or ".join(map(repr, choices)), partial(_is_choice, choices))
    if origin is Sequence:
        return _Items(_kind(get_args(option)[0], f"{path}[]"))
    if origin is Mapping:
        return _Named()
    return _block(cast("type[Any]", option), path)


def _block(block: type[Any], path: str) -> _Kind:
    """Compile an input dict, its keys, the value it builds and what it requires."""
    hints = get_type_hints(block)
    build = _BUILDS[block]
    order = [item.name for item in fields(build)] if is_dataclass(build) else hints
    compiled = _Block(
        {key: _kind(hint, _child(path, key)) for key, hint in hints.items()},
        build,
        resettable=any(
            get_origin(arg) is Replace
            for hint in hints.values()
            for arg in get_args(hint)
        ),
        required=tuple(key for key in order if key in block.__required_keys__),
    )
    return _AlternatesBlock(compiled) if block is AlternatesDict else compiled


_PAGE: Final = _block(MetadataDict, "")
_SITE: Final = _block(SiteMetadataDict, "")
_OWN: Final = frozenset(get_type_hints(MetadataDict)).intersection(
    item.name for item in fields(Segment)
)


def _segment(block: _Kind, raw: object, source: str) -> Segment:
    """Validate `raw` and split what the segment folds on its own from the rest."""
    walk = _Walk(source)
    checked = cast("dict[str, Any]", block.coerce(raw, walk=walk, path=""))
    own = {key: checked.pop(key) for key in _OWN if key in checked}
    return Segment(
        source, Metadata(**checked), replaced=frozenset(walk.replaced), **own
    )


def normalize_metadata(raw: object, *, source: str) -> Segment:
    """Validate one raw metadata mapping of a `page.py` strictly into a `Segment`.

    A `Promise` stays unevaluated, so a memoised lazy translation resolves per request.
    """
    return _segment(_PAGE, raw, source)


def normalize_site_metadata(raw: object, *, source: str) -> Segment:
    """Validate the settings defaults strictly into the outermost `Segment`."""
    return _segment(_SITE, raw, source)


__all__ = [
    "ICON_RELS",
    "LINK_ATTRS",
    "X_DEFAULT",
    "normalize_metadata",
    "normalize_site_metadata",
]
