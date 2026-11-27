"""Merged view of `settings.NEXT_FRAMEWORK` with framework defaults.

The merge runs on first access and caches until `reload()` emits `settings_reloaded`.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, ClassVar, override

from django.conf import settings

from next.caches import FRAMEWORK, SETTINGS
from next.checks import CHECKING
from next.errors import SettingShapeError

from .defaults import DEFAULTS, USER_SETTING
from .merge import BOOL_KEYS, LIST_KEYS, STR_KEYS, merge_user_settings, mistyped
from .signals import dispatch_settings_reloaded


if TYPE_CHECKING:
    from collections.abc import Mapping


class NextFrameworkSettings:
    """Lazy merged view of top-level keys declared in `DEFAULTS`.

    A read keeps its value in the instance dict, unless a reload overtook its merge.
    """

    DEFAULTS: ClassVar[Mapping[str, Any]] = DEFAULTS
    LIST_KEYS: ClassVar[frozenset[str]] = LIST_KEYS
    STR_KEYS: ClassVar[frozenset[str]] = STR_KEYS
    BOOL_KEYS: ClassVar[frozenset[str]] = BOOL_KEYS

    def __init__(self) -> None:
        """Initialise empty merge and attribute caches."""
        self._merged_cache: dict[str, Any] | None = None
        self._mistyped: dict[str, str] = {}
        self._epoch = 0
        self._lock = threading.Lock()

    def reload(self) -> None:
        """Drop the merge, move `SETTINGS` and `FRAMEWORK`, then emit the reload signal.

        The memos drop before any receiver runs, so a receiver never reads a stale one.
        """
        with self._lock:
            self._epoch += 1
            self._merged_cache = None
            kept = self.__dict__
            for key in self.DEFAULTS:
                kept.pop(key, None)
        SETTINGS.move()
        FRAMEWORK.move()
        dispatch_settings_reloaded(type(self))

    def _raw_user(self) -> dict[str, Any] | None:
        """Return the mapping the user set, or None for anything else under the key.

        An empty mapping needs no case of its own, the merge answers the defaults.
        """
        raw = getattr(settings, USER_SETTING, None)
        return raw if isinstance(raw, dict) else None

    def _merged(self) -> dict[str, Any]:
        merged = self._merged_cache
        if merged is None:
            epoch = self._epoch
            raw = self._raw_user()
            self._mistyped = mistyped(self.DEFAULTS, raw)
            merged = merge_user_settings(self.DEFAULTS, raw)
            with self._lock:
                if epoch == self._epoch:
                    self._merged_cache = merged
        return merged

    def mistyped(self) -> tuple[str, ...]:
        """Return a sentence for each top-level value the merge kept the default of."""
        self._merged()
        return tuple(self._mistyped.values())

    def __getattr__(self, attr: str) -> Any:
        """Return the merged value of a key declared in `DEFAULTS`, kept for next reads.

        A mistyped key reads as its default, but raises inside a check for it to yield.
        """
        if attr not in self.DEFAULTS:
            allowed = ", ".join(sorted(self.DEFAULTS))
            msg = f"Invalid Next framework setting: {attr!r}. Allowed keys: {allowed}."
            raise AttributeError(msg)
        epoch = self._epoch
        val = self._merged()[attr]
        problem = self._mistyped.get(attr)
        if problem is None:
            with self._lock:
                if epoch == self._epoch:
                    self.__dict__[attr] = val
        elif CHECKING.get():
            raise SettingShapeError((problem,))
        return val

    @override
    def __setattr__(self, name: str, value: object) -> None:
        """Allow only internal cache attributes and raise for declared keys."""
        if name in {"_merged_cache", "_mistyped"}:
            super().__setattr__(name, value)
            return
        if name in self.DEFAULTS:
            msg = (
                f"Setting {name!r} cannot be assigned. Update "
                f"settings.{USER_SETTING} instead."
            )
            raise AttributeError(msg)
        super().__setattr__(name, value)


next_framework_settings: NextFrameworkSettings = NextFrameworkSettings()


def fail_loudly() -> bool:
    """Whether a load failure is raised at its caller rather than logged away.

    Every fail-loud path reads this one predicate so `STRICT_LOADING` and `DEBUG` agree.
    """
    return bool(next_framework_settings.STRICT_LOADING or settings.DEBUG)


def refuse_mistyped(**kwargs: object) -> None:
    """Refuse each request while a top-level `NEXT_FRAMEWORK` value is mistyped.

    Startup and the checks read the default in its place, so the checks name it first.
    """
    found = next_framework_settings.mistyped()
    if found:
        raise SettingShapeError(found)


def reload_on_setting_changed(*, setting: str, **kwargs: object) -> None:
    """Move `SETTINGS` on any change Django reports, reloading on a framework one."""
    if setting == USER_SETTING:
        next_framework_settings.reload()
    else:
        SETTINGS.move()
