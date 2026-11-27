"""The claims a boundary token carries and the claims versions this code reads."""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Self


VERSION: Final = 2
"""The claims version every token is minted under."""

VERSIONS: Final[tuple[int, ...]] = (VERSION,)
"""The claims versions a redeem reads."""

type Scalar = str | int | float | bool | None
type Wire = Scalar | list[Scalar]
type Pairs[V] = tuple[tuple[str, V], ...]


def is_scalar(value: object) -> bool:
    """Whether `value` is a JSON scalar, a float only when it is finite."""
    if value is None or isinstance(value, str | int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def is_wire(value: object) -> bool:
    """Whether `value` is a scalar or a flat list of scalars, never a mapping."""
    if isinstance(value, list):
        return all(map(is_scalar, value))
    return is_scalar(value)


def url_pairs(kwargs: Mapping[str, object]) -> Pairs[str]:
    """Return URL kwargs sorted by name, each value as the text a URL carries."""
    return tuple(sorted((str(name), str(value)) for name, value in kwargs.items()))


@dataclass(frozen=True, slots=True)
class BoundaryClaims:
    """What a client-trip boundary token proves, the access rule never among it.

    `props` holds the wire form of `encode_props`, `issued` and `expires` Unix seconds.
    """

    component: str
    scope: str
    props: Pairs[Wire] = ()
    url_path: str = "/"
    url_kwargs: Pairs[str] = ()
    perms: tuple[str, ...] = ()
    issued: int = 0
    expires: int = 0
    version: int = VERSION

    @classmethod
    def of(
        cls,
        component: str,
        scope: str,
        *,
        props: Iterable[tuple[str, Wire]] = (),
        url_path: str = "/",
        url_kwargs: Mapping[str, object] | None = None,
        perms: Iterable[str] = (),
    ) -> Self:
        """Return the claims in canonical order, so equal claims give equal bytes."""
        return cls(
            component=component,
            scope=scope,
            props=tuple(sorted(props, key=lambda pair: pair[0])),
            url_path=url_path,
            url_kwargs=url_pairs(url_kwargs or {}),
            perms=tuple(sorted(set(perms))),
        )


__all__ = [
    "VERSION",
    "VERSIONS",
    "BoundaryClaims",
    "Pairs",
    "Scalar",
    "Wire",
    "is_scalar",
    "is_wire",
    "url_pairs",
]
