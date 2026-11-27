"""The settings one token mint or redeem runs under, a component's overrides merged."""

import hashlib
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Final, Self, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.utils.module_loading import import_string

from next.boundaries.options import TOKEN_OVERRIDES
from next.caches import pure_cache
from next.identity import SubjectBinding
from next.utils import MAX_DURATION

from .binding import build_binding
from .claims import VERSION, BoundaryClaims, Pairs


type MaxAge = Callable[[HttpRequest, BoundaryClaims], object]

_WHERE: Final = "NEXT_FRAMEWORK['DEFER']['TOKENS']['OPTIONS']"
_TOKENS: Final = "component.defer(tokens=...)"
_SESSIONS_APP: Final = "django.contrib.sessions"
_PROFILE_CHARS: Final = 12

PROJECT_PROFILE: Final = "0"
"""The profile a token minted under the project settings alone names in its header."""


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


@pure_cache(256)
def override_profile(tokens: Pairs[object]) -> str:
    """Return the name a token minted under one component's overrides carries in clear.

    It names settings anyone can read in the code, and the seal binds it to the token.
    """
    if not tokens:
        return PROJECT_PROFILE
    return hashlib.sha256(repr(tokens).encode()).hexdigest()[:_PROFILE_CHARS]


@dataclass(frozen=True, slots=True)
class TokenSettings:
    """The numbers and the binding one token runs under, validated as a whole."""

    binding: SubjectBinding
    max_age: int | MaxAge
    max_bytes: int
    max_stored_bytes: int
    cache_alias: str
    epoch: int
    profile: str = PROJECT_PROFILE

    def __post_init__(self) -> None:
        """Refuse a session binding in a project without the sessions app."""
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
        max_bytes = self.max_bytes
        if "max_bytes" in held:
            max_bytes = _count(held, "max_bytes", 1, _TOKENS)
        return replace(
            self,
            binding=binding,
            max_age=max_age,
            max_bytes=max_bytes,
            profile=override_profile(tokens),
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

    def stamped(
        self, request: HttpRequest, claims: BoundaryClaims, now: int
    ) -> BoundaryClaims:
        """Return the claims with their version, mint second and sealed expiry."""
        if isinstance(self.max_age, int):
            return replace(
                claims, version=VERSION, issued=now, expires=now + self.max_age
            )
        issued = replace(claims, version=VERSION, issued=now)
        return replace(issued, expires=now + self.lifetime(request, issued))

    def alive(self, request: HttpRequest, claims: BoundaryClaims, now: int) -> bool:
        """Whether verified claims live at `now`, the policy able to shorten their life.

        The sealed expiry caps whatever lifetime the policy answers for this request.
        """
        return now < min(claims.expires, claims.issued + self.lifetime(request, claims))


__all__ = ["PROJECT_PROFILE", "MaxAge", "TokenSettings", "override_profile"]
