"""Per-connection limits of a WebSocket, its ping interval and its upstream frames."""

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Self, TypeGuard

from django.core.exceptions import ImproperlyConfigured

from next.conf.merge import FROZEN_DEFAULTS
from next.conf.scopes import scope_value
from next.utils import int_between, positive_number

from .frames import MAX_INBOUND_BYTES


_UPSTREAM: Final[Mapping[str, int | float]] = FROZEN_DEFAULTS["REALTIME"]["UPSTREAM"]
_TUNNEL: Final[Mapping[str, int | float]] = FROZEN_DEFAULTS["REALTIME"]["TUNNEL"]
_HEARTBEAT: Final[float] = FROZEN_DEFAULTS["REALTIME"]["HEARTBEAT"]
UPSTREAM_KEYS: Final = frozenset(_UPSTREAM)
TUNNEL_KEYS: Final = frozenset(_TUNNEL)
MAX_RATE: Final = 1000
MAX_BURST: Final = 1000
MAX_CONCURRENCY: Final = 64
MAX_TIMEOUT: Final = 600
MAX_REPLY_BYTES: Final = 64 * 1024 * 1024


def valid_heartbeat(value: object) -> TypeGuard[float]:
    """Whether a `HEARTBEAT` value is a positive finite number of seconds."""
    return positive_number(value, math.inf)


def heartbeat_seconds() -> float:
    """Return the WebSocket ping interval of `HEARTBEAT`, the default for a bad one."""
    value = scope_value("REALTIME", "HEARTBEAT")
    return float(value) if valid_heartbeat(value) else _HEARTBEAT


def valid_rate(value: object) -> bool:
    """Whether an `UPSTREAM['RATE']` value is a positive number of frames a second."""
    return positive_number(value, MAX_RATE)


def valid_burst(value: object) -> bool:
    """Whether an `UPSTREAM['BURST']` value is a positive whole number of frames."""
    return int_between(value, 1, MAX_BURST)


def valid_max_bytes(value: object) -> bool:
    """Whether an `UPSTREAM['MAX_BYTES']` value fits inside one inbound frame."""
    return int_between(value, 1, MAX_INBOUND_BYTES)


def valid_concurrency(value: object) -> bool:
    """Whether an `UPSTREAM` or `TUNNEL` `CONCURRENCY` is a positive count of tasks."""
    return int_between(value, 1, MAX_CONCURRENCY)


def valid_timeout(value: object) -> bool:
    """Whether an `UPSTREAM` or `TUNNEL` `TIMEOUT` is a positive number of seconds."""
    return positive_number(value, MAX_TIMEOUT)


def valid_reply_bytes(value: object) -> bool:
    """Whether a `TUNNEL['MAX_REPLY_BYTES']` value is a positive byte count."""
    return int_between(value, 1, MAX_REPLY_BYTES)


@dataclass(frozen=True, slots=True)
class UpstreamLimits:
    """The token bucket the frames of a connection share and the bounds of `up`."""

    rate: float
    burst: int
    max_bytes: int
    concurrency: int
    timeout: float

    @classmethod
    def configured(cls) -> Self:
        """Read `REALTIME['UPSTREAM']`, raising `ImproperlyConfigured` for a bad one."""
        raw = scope_value("REALTIME", "UPSTREAM")
        if not isinstance(raw, Mapping) or not set(raw) <= UPSTREAM_KEYS:
            msg = (
                "REALTIME.UPSTREAM must be a mapping of RATE, BURST, MAX_BYTES, "
                "CONCURRENCY and TIMEOUT."
            )
            raise ImproperlyConfigured(msg)
        rate = raw.get("RATE", _UPSTREAM["RATE"])
        burst = raw.get("BURST", _UPSTREAM["BURST"])
        max_bytes = raw.get("MAX_BYTES", _UPSTREAM["MAX_BYTES"])
        concurrency = raw.get("CONCURRENCY", _UPSTREAM["CONCURRENCY"])
        timeout = raw.get("TIMEOUT", _UPSTREAM["TIMEOUT"])
        if not (
            valid_rate(rate)
            and valid_burst(burst)
            and valid_max_bytes(max_bytes)
            and valid_concurrency(concurrency)
            and valid_timeout(timeout)
        ):
            msg = "REALTIME.UPSTREAM holds a value out of range."
            raise ImproperlyConfigured(msg)
        return cls(
            float(rate), int(burst), int(max_bytes), int(concurrency), float(timeout)
        )


@dataclass(frozen=True, slots=True)
class TunnelLimits:
    """How many tunneled requests a connection runs at once, for how long, how big."""

    concurrency: int
    timeout: float
    max_reply_bytes: int

    @classmethod
    def configured(cls) -> Self | None:
        """Read `REALTIME['TUNNEL']`, None when it is False and the tunnel is off."""
        raw = scope_value("REALTIME", "TUNNEL")
        if raw is False:
            return None
        if not isinstance(raw, Mapping) or not set(raw) <= TUNNEL_KEYS:
            msg = (
                "REALTIME.TUNNEL must be False or a mapping of CONCURRENCY, TIMEOUT "
                "and MAX_REPLY_BYTES."
            )
            raise ImproperlyConfigured(msg)
        concurrency = raw.get("CONCURRENCY", _TUNNEL["CONCURRENCY"])
        timeout = raw.get("TIMEOUT", _TUNNEL["TIMEOUT"])
        reply = raw.get("MAX_REPLY_BYTES", _TUNNEL["MAX_REPLY_BYTES"])
        if not (
            valid_concurrency(concurrency)
            and valid_timeout(timeout)
            and valid_reply_bytes(reply)
        ):
            msg = "REALTIME.TUNNEL holds a value out of range."
            raise ImproperlyConfigured(msg)
        return cls(int(concurrency), float(timeout), int(reply))


@dataclass(frozen=True, slots=True)
class SocketLimits:
    """The upstream and tunnel limits of a connection, None for a disabled tunnel."""

    upstream: UpstreamLimits
    tunnel: TunnelLimits | None

    @classmethod
    def configured(cls) -> Self:
        """Read both settings, raising `ImproperlyConfigured` for a bad one."""
        return cls(UpstreamLimits.configured(), TunnelLimits.configured())


class TokenBucket:
    """Admit frames at a steady rate with room for a burst, on the socket's clock."""

    __slots__ = ("_burst", "_clock", "_rate", "_since", "_tokens")

    def __init__(
        self, rate: float, burst: int, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Start full, so a fresh connection may spend its whole burst at once."""
        self._rate = rate
        self._burst = float(burst)
        self._clock = clock
        self._tokens = float(burst)
        self._since = clock()

    def take(self) -> bool:
        """Spend one token, False when the bucket is empty and the frame is refused."""
        now = self._clock()
        elapsed = max(0.0, now - self._since)
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._since = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


__all__ = [
    "MAX_BURST",
    "MAX_CONCURRENCY",
    "MAX_RATE",
    "MAX_REPLY_BYTES",
    "MAX_TIMEOUT",
    "TUNNEL_KEYS",
    "UPSTREAM_KEYS",
    "SocketLimits",
    "TokenBucket",
    "TunnelLimits",
    "UpstreamLimits",
    "heartbeat_seconds",
    "valid_burst",
    "valid_concurrency",
    "valid_heartbeat",
    "valid_max_bytes",
    "valid_rate",
    "valid_reply_bytes",
    "valid_timeout",
]
