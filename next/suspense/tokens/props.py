"""The typed codec of boundary props, where a model travels as its primary key."""

import math
import types
import typing
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Final, cast
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Model
from django.http import Http404, HttpRequest
from django.utils.safestring import SafeString

from next.suspense.errors import (
    BoundaryPropsError,
    BoundarySizeError,
    BoundaryTokenError,
    BoundaryValueError,
)
from next.suspense.markers import TokenRejection

from .claims import Pairs, Scalar, Wire


DEFAULT_LIMIT: Final = 2048
"""The longest text one prop value holds when no `MAX_BYTES` is passed."""

type Fetch = Callable[[HttpRequest, type[Model], object], Model]

_NONE: Final = type(None)
_SCALARS: Final = (str, int, float, bool, Decimal, date, datetime, UUID)
_INT_BOUND: Final = 2**63
_REFUSED: Final = (TypeError, ValueError, ArithmeticError)
_UNANNOTATED: Final = object()
_ANNOTATED_ONLY: Final = (Decimal, date, UUID, Model)


def fetch(request: HttpRequest, model: type[Model], pk: object) -> Model:
    """Return the row of `model` with `pk` through its default manager."""
    del request
    return model._default_manager.get(pk=pk)


@dataclass(frozen=True, slots=True)
class _Spec:
    """What one annotation accepts, a scalar type or a model, maybe listed or None."""

    kind: type
    many: bool = False
    optional: bool = False


def _refused(name: str, annotation: object) -> BoundaryPropsError:
    return BoundaryPropsError(
        f"the prop {name!r} is annotated {annotation!r}, which no token carries. "
        "Use str, int, float, bool, None, Decimal, date, datetime, UUID, list[X] of "
        "those, or a concrete model class, and write an optional one as X | None."
    )


def _arms(annotation: object) -> tuple[object, ...] | None:
    """Return the arms of a union annotation, `None` for any other annotation."""
    if isinstance(annotation, types.UnionType):
        return typing.get_args(annotation)
    return None


def _single(name: str, annotation: object, *, models: bool) -> type:
    """Return the scalar class or, where `models` allows, the model an annotation is."""
    if any(annotation is scalar for scalar in _SCALARS):
        return typing.cast("type", annotation)
    if (
        models
        and isinstance(annotation, type)
        and issubclass(annotation, Model)
        and not annotation._meta.abstract
    ):
        return annotation
    raise _refused(name, annotation)


def _spec(name: str, annotation: object) -> _Spec:
    """Read what one prop annotation accepts, refusing every other shape."""
    if annotation is None or annotation is _NONE:
        return _Spec(_NONE)
    arms = _arms(annotation)
    if arms is not None:
        rest = [arm for arm in arms if arm is not _NONE]
        if len(rest) != 1 or len(rest) == len(arms):
            raise _refused(name, annotation)
        return replace(_spec(name, rest[0]), optional=True)
    if isinstance(annotation, types.GenericAlias) and annotation.__origin__ is list:
        elements = typing.get_args(annotation)
        if len(elements) != 1:
            raise _refused(name, annotation)
        return _Spec(_single(name, elements[0], models=False), many=True)
    return _Spec(_single(name, annotation, models=True))


class _NonFiniteError(ValueError):
    """A number of a carried type that is NaN or infinite."""


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError
    if not -_INT_BOUND <= value < _INT_BOUND:
        raise OverflowError
    return int(value)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError
    number = float(value)
    if not math.isfinite(number):
        raise _NonFiniteError
    return number


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError
    return str(value)


def _flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError
    return value


def _decimal(value: object) -> str:
    if not isinstance(value, Decimal):
        raise TypeError
    if not value.is_finite():
        raise _NonFiniteError
    return str(value)


def _day(value: object) -> str:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError
    return value.isoformat()


def _moment(value: object) -> str:
    if not isinstance(value, datetime):
        raise TypeError
    return value.isoformat()


def _uuid(value: object) -> str:
    if not isinstance(value, UUID):
        raise TypeError
    return str(value)


_ENCODERS: Final[Mapping[type, Callable[[object], Scalar]]] = {
    str: _text,
    int: _integer,
    float: _number,
    bool: _flag,
    Decimal: _decimal,
    date: _day,
    datetime: _moment,
    UUID: _uuid,
}


def _pk_wire(pk: object) -> int | str:
    """Return how a primary key travels, an integer as one and the rest as text."""
    if isinstance(pk, int) and not isinstance(pk, bool):
        return _integer(pk)
    if isinstance(pk, str | UUID):
        return str(pk)
    raise TypeError


def _limited(name: str, wire: Scalar, limit: int) -> Scalar:
    if isinstance(wire, str) and len(wire) > limit:
        msg = f"the prop {name!r} holds text over {limit} characters"
        raise BoundarySizeError(msg)
    return wire


def _overflow(name: str) -> BoundarySizeError:
    return BoundarySizeError(f"the prop {name!r} holds a number outside 64 bits")


def _non_finite(name: str, value: object) -> BoundaryValueError:
    return BoundaryValueError(f"the prop {name!r} holds {value}, which is not finite")


def _encode_one(name: str, value: object, kind: type, limit: int) -> Scalar:
    """Return the wire form of one scalar or model value, refusing a misfit."""
    try:
        if issubclass(kind, Model):
            if not isinstance(value, kind) or value.pk is None:
                raise TypeError
            wire: Scalar = _pk_wire(value.pk)
        else:
            wire = _ENCODERS[kind](value)
    except OverflowError as exc:
        raise _overflow(name) from exc
    except _NonFiniteError as exc:
        raise _non_finite(name, value) from exc
    except _REFUSED as exc:
        msg = f"the prop {name!r} holds a {type(value).__name__}, not a {kind.__name__}"
        raise BoundaryPropsError(msg) from exc
    return _limited(name, wire, limit)


def _native(value: object) -> Scalar:
    """Return a value whose JSON type names its Python type, refusing any other.

    Text marked safe travels as plain text, the one value whose type it changes.
    """
    kind = type(value)
    if value is None or kind is bool:
        return cast("Scalar", value)
    if kind is int:
        return _integer(value)
    if kind is float:
        return _number(value)
    if kind is str or kind is SafeString:
        return str.__str__(cast("str", value))
    raise TypeError


def _unannotated_misfit(name: str, value: object) -> BoundaryPropsError:
    shown = type(value).__name__
    if isinstance(value, _ANNOTATED_ONLY):
        return BoundaryPropsError(
            f"the prop {name!r} holds a {shown}, which a token carries only when a "
            f"@component.context function annotates its parameter {name!r}"
        )
    return BoundaryPropsError(
        f"the prop {name!r} holds a {shown}, which no token carries unannotated. "
        "Pass str, int, float, bool, None or a list of those, or annotate the "
        f"parameter {name!r} of a @component.context function"
    )


def _unannotated_one(name: str, value: object, limit: int) -> Scalar:
    try:
        wire = _native(value)
    except OverflowError as exc:
        raise _overflow(name) from exc
    except _NonFiniteError as exc:
        raise _non_finite(name, value) from exc
    except _REFUSED as exc:
        raise _unannotated_misfit(name, value) from exc
    return _limited(name, wire, limit)


def _encode_unannotated(name: str, value: object, limit: int) -> Wire:
    """Return the wire form of a prop no annotation types, which reads back as is."""
    if type(value) is list:
        return [_unannotated_one(name, item, limit) for item in value]
    return _unannotated_one(name, value, limit)


def _encode(name: str, value: object, annotation: object, limit: int) -> Wire:
    """Return the wire form of one prop value under its annotation, if it has one."""
    if isinstance(value, Mapping):
        msg = f"the prop {name!r} is a mapping, and a token carries no dict"
        raise BoundaryPropsError(msg)
    if annotation is _UNANNOTATED:
        return _encode_unannotated(name, value, limit)
    spec = _spec(name, annotation)
    if value is None and (spec.optional or spec.kind is _NONE):
        return None
    if spec.kind is _NONE:
        msg = f"the prop {name!r} is annotated None but holds a value"
        raise BoundaryPropsError(msg)
    if spec.many:
        if not isinstance(value, list | tuple):
            msg = f"the prop {name!r} is annotated as a list but holds no list"
            raise BoundaryPropsError(msg)
        return [_encode_one(name, item, spec.kind, limit) for item in value]
    return _encode_one(name, value, spec.kind, limit)


def encode_props(
    props: Mapping[str, object],
    types: Mapping[str, object],
    *,
    limit: int = DEFAULT_LIMIT,
) -> Pairs[Wire]:
    """Return the wire form of `props`, each typed by `types` or by its own JSON type.

    A misfit raises `BoundaryPropsError`, a value too large or not finite a subclass.
    """
    return tuple(
        (name, _encode(name, props[name], types.get(name, _UNANNOTATED), limit))
        for name in sorted(props)
    )


def check_annotation(name: str, annotation: object) -> None:
    """Raise `BoundaryPropsError` when no token carries a prop of `annotation`."""
    _spec(name, annotation)


def _malformed() -> BoundaryTokenError:
    return BoundaryTokenError(TokenRejection.MALFORMED)


def _read_text(value: object, limit: int) -> str:
    if type(value) is not str or len(value) > limit:
        raise TypeError
    return value


def _read_integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError
    return _integer(value)


def _read_number(value: object) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise TypeError
    return value


def _read_flag(value: object) -> bool:
    if type(value) is not bool:
        raise TypeError
    return value


def _canonical[T](value: T, written: str, text: str) -> T:
    """Return `value` when it writes back as `text`, so no alternate spelling passes."""
    if written != text:
        raise ValueError
    return value


def _read_decimal(text: str) -> Decimal:
    number = Decimal(text)
    if not number.is_finite():
        raise ValueError
    return _canonical(number, str(number), text)


def _read_day(text: str) -> date:
    day = date.fromisoformat(text)
    return _canonical(day, day.isoformat(), text)


def _read_moment(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return _canonical(moment, moment.isoformat(), text)


def _read_uuid(text: str) -> UUID:
    uuid = UUID(text)
    return _canonical(uuid, str(uuid), text)


_READERS: Final[Mapping[type, Callable[[object], object]]] = {
    int: _read_integer,
    float: _read_number,
    bool: _read_flag,
}

_TEXT_READERS: Final[Mapping[type, Callable[[str], object]]] = {
    str: str,
    Decimal: _read_decimal,
    date: _read_day,
    datetime: _read_moment,
    UUID: _read_uuid,
}
"""The readers of the types that travel as text, each given text within the limit."""


def _pk(value: object, model: type[Model], limit: int) -> object:
    """Return the primary key a wire value names, refusing any value it coerces."""
    if type(value) is str:
        _read_text(value, limit)
    elif type(value) is not int:
        raise TypeError
    typed = model._meta.pk.to_python(value)
    wire = _pk_wire(typed)
    if type(wire) is not type(value) or wire != value:
        raise ValueError
    return typed


def _row(
    request: HttpRequest, value: object, model: type[Model], fetch: Fetch, limit: int
) -> Model:
    """Return the row a primary key names, `Http404` once the row is gone."""
    try:
        pk = _pk(value, model, limit)
    except (*_REFUSED, ValidationError) as exc:
        raise _malformed() from exc
    try:
        return fetch(request, model, pk)
    except ObjectDoesNotExist as exc:
        msg = "The row a boundary prop names no longer exists."
        raise Http404(msg) from exc


def _read(
    request: HttpRequest, value: object, kind: type, fetch: Fetch, limit: int
) -> object:
    """Return one scalar or model of its wire value, malformed for any other type."""
    if issubclass(kind, Model):
        return _row(request, value, kind, fetch, limit)
    try:
        reader = _READERS.get(kind)
        if reader is not None:
            return reader(value)
        return _TEXT_READERS[kind](_read_text(value, limit))
    except _REFUSED as exc:
        raise _malformed() from exc


def _read_native(value: object, limit: int) -> Scalar:
    """Return an unannotated scalar of its wire value, its JSON type its Python type."""
    try:
        read = _native(value)
        return _read_text(read, limit) if isinstance(read, str) else read
    except _REFUSED as exc:
        raise _malformed() from exc


def _decode_unannotated(value: object, limit: int) -> Wire:
    if type(value) is list:
        return [_read_native(item, limit) for item in value]
    return _read_native(value, limit)


def _decode(
    request: HttpRequest,
    prop: tuple[str, object],
    annotation: object,
    fetch: Fetch,
    limit: int,
) -> object:
    """Return one prop of its wire value, strictly of the type its annotation names."""
    name, value = prop
    if annotation is _UNANNOTATED:
        return _decode_unannotated(value, limit)
    spec = _spec(name, annotation)
    if value is None and (spec.optional or spec.kind is _NONE):
        return None
    if value is None or spec.kind is _NONE:
        raise _malformed()
    if spec.many:
        if type(value) is not list:
            raise _malformed()
        return [_read(request, item, spec.kind, fetch, limit) for item in value]
    return _read(request, value, spec.kind, fetch, limit)


def decode_props(
    request: HttpRequest,
    raw: Mapping[str, object] | Iterable[tuple[str, object]],
    types: Mapping[str, object],
    *,
    fetch: Fetch = fetch,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, object]:
    """Return the props a verified token carries, typed by `types` or by the wire.

    A wire value of another JSON type is `malformed`, never coerced.
    """
    pairs = raw.items() if isinstance(raw, Mapping) else raw
    return {
        prop[0]: _decode(request, prop, types.get(prop[0], _UNANNOTATED), fetch, limit)
        for prop in pairs
    }


__all__ = [
    "DEFAULT_LIMIT",
    "Fetch",
    "check_annotation",
    "decode_props",
    "encode_props",
    "fetch",
]
