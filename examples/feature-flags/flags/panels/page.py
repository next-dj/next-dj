from flags.models import Flag

from next import context


@context("active_flags")
def active_flags() -> list[Flag]:
    return list(Flag.objects.filter(enabled=True))


@context("disabled_flags")
def disabled_flags() -> list[Flag]:
    return list(Flag.objects.filter(enabled=False))
