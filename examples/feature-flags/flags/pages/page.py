from flags.models import Flag
from flags.services import flags_in_state

from next import context


@context("active_flags")
def active_flags() -> list[Flag]:
    return flags_in_state(enabled=True)


@context("disabled_flags")
def disabled_flags() -> list[Flag]:
    return flags_in_state(enabled=False)
