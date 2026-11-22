"""The settings one token mint or redeem runs under, a component's overrides merged."""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Final, Self, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.utils.module_loading import import_string

from next.defer import MAX_DURATION, TOKEN_OVERRIDES
from next.identity import SubjectBinding

from .binding import build_binding
from .claims import VERSION, BoundaryClaims, Pairs


type MaxAge = Callable[[HttpRequest, BoundaryClaims], object]

_WHERE: Final = "NEXT_FRAMEWORK['DEFER']['TOKENS']['OPTIONS']"
_TOKENS: Final = "component.defer(tokens=...)"
_SESSIONS_APP: Final = "django.contrib.sessions"


def _count(
    options: Mapping[str, object], key: str, least: int, where: str = _WHERE
) -> int:
    value = options.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        msg = f"{where}[{key!r}] must be an integer from {least}, got {value!r}."
        raise ImproperlyConfigured(msg)
    return value


def _max_age(value: object) -> int | MaxAge:
    """Return a lifetime in seconds or the callable a dotted path names."""
    if isinstance(value, str):
        try:
            target = import_string(value)
        except ImportError as exc:
            msg = f"{_WHERE}['MAX_AGE'] {value!r} does not import."
            raise ImproperlyConfigured(msg) from exc
        if not callable(target):
            msg = f"{_WHERE}['MAX_AGE'] {value!r} names no callable."
            raise ImproperlyConfigured(msg)
        return cast("MaxAge", target)
    return _count({"MAX_AGE": value}, "MAX_AGE", 1)


def _bucket(value: object, where: str = _WHERE) -> int | None:
    return None if value is None else _count({"BUCKET": value}, "BUCKET", 0, where)


@dataclass(frozen=True, slots=True)
class TokenSettings:
    """The numbers and the binding one token runs under, validated as a whole."""

    binding: SubjectBinding
    max_age: int | MaxAge
    bucket: int | None
    max_bytes: int
    max_stored_bytes: int
    cache_alias: str
    epoch: int

    def __post_init__(self) -> None:
        """Refuse a bucket from the lifetime and a session binding without sessions."""
        if (
            self.bucket
            and isinstance(self.max_age, int)
            and self.bucket >= self.max_age
        ):
            msg = (
                f"the token BUCKET {self.bucket} must stay below MAX_AGE {self.max_age}"
            )
            raise ImproperlyConfigured(msg)
        if self.binding.needs_sessions and not apps.is_installed(_SESSIONS_APP):
            msg = "a session-bound boundary token needs django.contrib.sessions"
            raise ImproperlyConfigured(msg)

    @classmethod
    def read(cls, options: Mapping[str, object]) -> Self:
        """Return the settings of a merged `OPTIONS` mapping, raising on a misfit."""
        binding = options.get("BINDING")
        alias = options.get("CACHE_ALIAS")
        if not isinstance(binding, str) or not isinstance(alias, str) or not alias:
            msg = f"{_WHERE} names its BINDING and CACHE_ALIAS as text."
            raise ImproperlyConfigured(msg)
        return cls(
            binding=build_binding(binding),
            max_age=_max_age(options.get("MAX_AGE")),
            bucket=_bucket(options.get("BUCKET")),
            max_bytes=_count(options, "MAX_BYTES", 1),
            max_stored_bytes=_count(options, "MAX_STORED_BYTES", 1),
            cache_alias=alias,
            epoch=_count(options, "EPOCH", 0),
        )

    def overridden(self, tokens: Pairs[object]) -> Self:
        """Return the settings with one component's `tokens` overrides merged over."""
        if not tokens:
            return self
        held = dict(tokens)
        unknown = sorted(set(held) - TOKEN_OVERRIDES)
        if unknown:
            msg = f"the component token overrides {unknown} have no use here"
            raise ImproperlyConfigured(msg)
        binding = held.get("binding", self.binding)
        if isinstance(binding, str):
            binding = build_binding(binding)
        if not isinstance(binding, SubjectBinding):
            msg = f"the component token binding {binding!r} is no name or dotted path"
            raise ImproperlyConfigured(msg)
        max_age = self.max_age
        if "max_age" in held:
            max_age = _count(held, "max_age", 1, _TOKENS)
        bucket = self.bucket
        if "bucket" in held:
            bucket = _bucket(held["bucket"], _TOKENS)
        max_bytes = self.max_bytes
        if "max_bytes" in held:
            max_bytes = _count(held, "max_bytes", 1, _TOKENS)
        return replace(
            self, binding=binding, max_age=max_age, bucket=bucket, max_bytes=max_bytes
        )

    def lifetime(self, request: HttpRequest, claims: BoundaryClaims) -> int:
        """Return the seconds a token lives, a callable answer clamped to one day."""
        if isinstance(self.max_age, int):
            return self.max_age
        value = self.max_age(request, claims)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            msg = f"the token MAX_AGE callable returned {value!r}, not a number"
            raise ImproperlyConfigured(msg)
        return min(max(int(value), 1), MAX_DURATION)

    def issued(self, now: int) -> int:
        """Return the mint second floored to `BUCKET`, so equal claims match in one."""
        if not self.bucket:
            return now
        return now - now % self.bucket

    def stamped(
        self, request: HttpRequest, claims: BoundaryClaims, now: int
    ) -> BoundaryClaims:
        """Return the claims with their version, mint second and sealed expiry.

        A bucket that reaches the lifetime a callable answers is refused, not minted.
        """
        minted = self.issued(now)
        if isinstance(self.max_age, int):
            return replace(
                claims, version=VERSION, issued=minted, expires=minted + self.max_age
            )
        issued = replace(claims, version=VERSION, issued=minted)
        lifetime = self.lifetime(request, issued)
        if self.bucket and self.bucket >= lifetime:
            msg = (
                f"the token BUCKET {self.bucket} reaches the {lifetime} seconds the "
                "MAX_AGE callable answered, which would mint tokens born expired"
            )
            raise ImproperlyConfigured(msg)
        return replace(issued, expires=issued.issued + lifetime)

    def alive(self, request: HttpRequest, claims: BoundaryClaims, now: int) -> bool:
        """Whether verified claims live at `now`, the policy able to shorten their life.

        The sealed expiry caps whatever lifetime the policy answers for this request.
        """
        return now < min(claims.expires, claims.issued + self.lifetime(request, claims))


__all__ = ["MaxAge", "TokenSettings"]
