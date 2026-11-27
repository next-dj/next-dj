"""The strict JSON that turns boundary claims into bytes and back, never pickle."""

import json
from typing import Final, NoReturn, cast

from next.defer.errors import BoundaryPropsError, BoundaryTokenError
from next.defer.markers import TokenRejection

from .claims import BoundaryClaims, Wire, is_wire


_KEYS: Final = frozenset({"c", "e", "i", "k", "m", "p", "s", "u", "v"})


def envelope(claims: BoundaryClaims) -> dict[str, object]:
    """Return the claims as one mapping of short keys, each level in sorted order."""
    if not all(is_wire(value) for _name, value in claims.props):
        msg = "a boundary prop holds a value outside the wire form of encode_props"
        raise BoundaryPropsError(msg)
    return {
        "c": claims.component,
        "e": claims.expires,
        "i": claims.issued,
        "k": dict(sorted(claims.url_kwargs)),
        "m": list(claims.perms),
        "p": dict(sorted(claims.props)),
        "s": claims.scope,
        "u": claims.url_path,
        "v": claims.version,
    }


def _malformed() -> NoReturn:
    raise BoundaryTokenError(TokenRejection.MALFORMED)


def _number(value: object) -> bool:
    return type(value) is int and value >= 0


def _text_map(value: object) -> bool:
    return isinstance(value, dict) and all(
        type(key) is str and type(item) is str for key, item in value.items()
    )


def _texts(value: object) -> bool:
    return isinstance(value, list) and all(type(item) is str for item in value)


def _props(value: object) -> bool:
    return isinstance(value, dict) and all(
        type(key) is str and is_wire(item) for key, item in value.items()
    )


def claims_of(held: object, version: int) -> BoundaryClaims:
    """Return the claims of a decoded mapping, refusing any key or type out of place."""
    if not isinstance(held, dict) or held.keys() != _KEYS:
        _malformed()
    fits = (
        _number(held["v"])
        and held["v"] == version
        and all(type(held[key]) is str for key in "csu")
        and _number(held["i"])
        and _number(held["e"])
        and _text_map(held["k"])
        and _texts(held["m"])
        and _props(held["p"])
    )
    if not fits:
        _malformed()
    props = cast("dict[str, Wire]", held["p"])
    return BoundaryClaims(
        component=held["c"],
        scope=held["s"],
        props=tuple(sorted(props.items())),
        url_path=held["u"],
        url_kwargs=tuple(sorted(cast("dict[str, str]", held["k"]).items())),
        perms=tuple(cast("list[str]", held["m"])),
        issued=held["i"],
        expires=held["e"],
        version=held["v"],
    )


def _constant(name: str) -> NoReturn:
    msg = f"JSON constant {name} carries no finite number"
    raise ValueError(msg)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    held = dict(pairs)
    if len(held) != len(pairs):
        msg = "a JSON object repeats a key"
        raise ValueError(msg)
    return held


_ENCODER: Final = json.JSONEncoder(
    sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
)
_DECODER: Final = json.JSONDecoder(parse_constant=_constant, object_pairs_hook=_unique)


def dumps(claims: BoundaryClaims) -> bytes:
    """Return claims as compact JSON with sorted keys, equal for equal claims."""
    held = envelope(claims)
    try:
        text = _ENCODER.encode(held)
    except ValueError as exc:
        msg = "a boundary prop holds a number JSON cannot carry"
        raise BoundaryPropsError(msg) from exc
    return text.encode()


def loads(data: bytes, version: int) -> BoundaryClaims:
    """Return the claims `data` holds under `version`, else refuse it malformed."""
    try:
        held = _DECODER.decode(data.decode())
    except (ValueError, RecursionError) as exc:
        raise BoundaryTokenError(TokenRejection.MALFORMED) from exc
    return claims_of(held, version)


__all__ = ["claims_of", "dumps", "envelope", "loads"]
