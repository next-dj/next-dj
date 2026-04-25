from __future__ import annotations

from flags.models import Flag

from next.pages import context


@context("active_flags")
def active_flags() -> list[Flag]:
    return list(Flag.objects.filter(enabled=True))


@context("disabled_flags")
def disabled_flags() -> list[Flag]:
    return list(Flag.objects.filter(enabled=False))
