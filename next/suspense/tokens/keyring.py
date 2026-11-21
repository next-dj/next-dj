"""The ring of keys boundary tokens are sealed with, the first sealing, all opening."""

import threading
from typing import TYPE_CHECKING, Final, NamedTuple

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from next.defer import MIN_KEY_LENGTH

from .clock import token_clock


if TYPE_CHECKING:
    from collections.abc import Callable


REFRESH: Final = 60
"""The seconds the answer of a key ring callable is held before it is asked again."""

_WHERE: Final = "NEXT_FRAMEWORK['DEFER']['TOKENS']['OPTIONS']['KEYS']"
_SECRETS: Final = "SECRET_KEY with SECRET_KEY_FALLBACKS, the ring while KEYS is None,"


def _checked(keys: object, source: str) -> tuple[str, ...]:
    """Return `keys` as a tuple, refusing an empty ring or a key under the minimum."""
    if not isinstance(keys, list | tuple) or not keys:
        msg = f"{source} gave no list of keys, so no boundary token can be sealed."
        raise ImproperlyConfigured(msg)
    if not all(isinstance(key, str) and len(key) >= MIN_KEY_LENGTH for key in keys):
        msg = (
            f"{source} gave a key shorter than {MIN_KEY_LENGTH} characters, which "
            "would let anyone forge boundary tokens. Generate each key with "
            "secrets.token_urlsafe(32)."
        )
        raise ImproperlyConfigured(msg)
    return tuple(keys)


class _Held(NamedTuple):
    keys: tuple[str, ...]
    since: int
    until: int


class KeyRing:
    """Hold the configured keys, the project secrets when none are named.

    Every ring is checked on each read, a callable's answer held for `REFRESH` seconds.
    """

    __slots__ = ("_held", "_keys", "_lock", "_source")

    def __init__(self, value: object = None) -> None:
        """Read `KEYS`, a list of keys, a dotted callable, or `None` for the secrets."""
        self._keys: tuple[str, ...] | None = None
        self._source: Callable[[], object] | None = None
        self._held: _Held | None = None
        self._lock = threading.Lock()
        if isinstance(value, str):
            try:
                source = import_string(value)
            except ImportError as exc:
                msg = f"{_WHERE} {value!r} does not import."
                raise ImproperlyConfigured(msg) from exc
            if not callable(source):
                msg = f"{_WHERE} {value!r} names no callable."
                raise ImproperlyConfigured(msg)
            self._source = source
        elif value is not None:
            self._keys = _checked(value, _WHERE)

    def current(self) -> tuple[str, ...]:
        """Return the keys now, the sealing one first."""
        if self._keys is not None:
            return self._keys
        if self._source is None:
            secrets = (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS)
            return _checked(secrets, _SECRETS)
        now = token_clock().now()
        held = self._held
        if held is not None and held.since <= now < held.until:
            return held.keys
        with self._lock:
            held = self._held
            if held is None or not held.since <= now < held.until:
                keys = _checked(self._source(), f"The key ring callable of {_WHERE}")
                held = _Held(keys, now, now + REFRESH)
                self._held = held
            return held.keys


__all__ = ["REFRESH", "KeyRing"]
