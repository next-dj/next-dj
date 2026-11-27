"""Shared system-check tags and the registration every `next-dj` check goes through."""

import functools
from collections.abc import Callable
from contextvars import ContextVar

from django.core import checks

from next.errors import SettingError


NEXT: str = "next"
"""Shared system-check tag that selects every `next-dj` check."""

SEO: str = "seo"
"""System-check tag selecting the `next.seo` checks."""

CHECKING: ContextVar[bool] = ContextVar("next_checking", default=False)
"""Whether a framework check runs, so a read of a mistyped setting raises to yield."""

type Check = Callable[..., list[checks.CheckMessage]]


def register(*tags: str, deploy: bool = False) -> Callable[[Check], Check]:
    """Register a check under `tags` that yields to the check of an unusable setting.

    That check names the value, so one mistake reads as one report rather than a crash.
    """

    def wrap(check: Check) -> Check:
        @functools.wraps(check)
        def run(*args: object, **kwargs: object) -> list[checks.CheckMessage]:
            token = CHECKING.set(True)
            try:
                return check(*args, **kwargs)
            except SettingError:
                return []
            finally:
                CHECKING.reset(token)

        checks.register(*tags, deploy=deploy)(run)
        return run

    return wrap


__all__ = ["NEXT", "SEO", "register"]
