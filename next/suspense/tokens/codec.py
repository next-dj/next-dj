"""The codecs that turn boundary claims into bytes and back, never through pickle."""

import functools
import importlib
import json
from abc import ABC, abstractmethod
from types import ModuleType
from typing import Final, NoReturn, cast, override

from django.core.exceptions import ImproperlyConfigured

from next.suspense.errors import BoundaryPropsError, BoundaryTokenError
from next.suspense.markers import TokenRejection

from .claims import BoundaryClaims, Wire, is_wire


_KEYS: Final = frozenset({"c", "e", "i", "k", "m", "p", "s", "u", "v"})


class TokenCodec(ABC):
    """Turn claims into bytes and back, the bytes equal for equal claims."""

    @classmethod
    def available(cls) -> bool:
        """Whether the library the codec writes with imports in this process."""
        return True

    @abstractmethod
    def dumps(self, claims: BoundaryClaims) -> bytes:
        """Return the bytes of `claims`, raising `BoundaryPropsError` for a misfit."""

    @abstractmethod
    def loads(self, data: bytes, version: int) -> BoundaryClaims:
        """Return the claims `data` holds under `version`, else refuse it malformed."""


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


class JsonCodec(TokenCodec):
    """Write claims as compact JSON with sorted keys, read them back strictly."""

    @override
    def dumps(self, claims: BoundaryClaims) -> bytes:
        held = envelope(claims)
        try:
            text = _ENCODER.encode(held)
        except ValueError as exc:
            msg = "a boundary prop holds a number JSON cannot carry"
            raise BoundaryPropsError(msg) from exc
        return text.encode()

    @override
    def loads(self, data: bytes, version: int) -> BoundaryClaims:
        try:
            held = _DECODER.decode(data.decode())
        except (ValueError, RecursionError) as exc:
            raise BoundaryTokenError(TokenRejection.MALFORMED) from exc
        return claims_of(held, version)


@functools.cache
def msgpack_library() -> ModuleType | None:
    """Return `msgpack`, `None` when it does not import, once per process."""
    try:
        return importlib.import_module("msgpack")
    except ImportError:
        return None


class MsgpackCodec(TokenCodec):
    """Write claims as MessagePack, available once the `msgpack` package imports."""

    def __init__(self) -> None:
        """Refuse to build without the library, so a project learns it at startup."""
        library = msgpack_library()
        if library is None:
            msg = (
                "MsgpackCodec needs the msgpack package, which does not import. "
                "Install msgpack or keep the default JsonCodec."
            )
            raise ImproperlyConfigured(msg)
        self._library = library

    @classmethod
    @override
    def available(cls) -> bool:
        return msgpack_library() is not None

    @override
    def dumps(self, claims: BoundaryClaims) -> bytes:
        held = envelope(claims)
        try:
            packed = self._library.packb(held, use_bin_type=True)
        except (TypeError, ValueError, OverflowError) as exc:
            msg = "a boundary prop holds a value MessagePack cannot carry"
            raise BoundaryPropsError(msg) from exc
        return cast("bytes", packed)

    @override
    def loads(self, data: bytes, version: int) -> BoundaryClaims:
        try:
            held = self._library.unpackb(
                data, raw=False, strict_map_key=True, use_list=True
            )
        except (TypeError, ValueError) as exc:
            raise BoundaryTokenError(TokenRejection.MALFORMED) from exc
        return claims_of(held, version)


__all__ = [
    "JsonCodec",
    "MsgpackCodec",
    "TokenCodec",
    "claims_of",
    "envelope",
    "msgpack_library",
]
