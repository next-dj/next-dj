"""The third-party scripts a page tree declares and the strategies that load them."""

import enum
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from next.consent import NECESSARY


class Strategy(enum.StrEnum):
    """When a script loads.

    The server renders `BLOCKING`, `ASYNC` and `DEFER` in the head when consent allows.
    The runtime loads the rest from the manifest, `IDLE` after the load event,
    `INTERACTION` on the first input, and `MANUAL` when the page asks for it.
    """

    BLOCKING = "blocking"
    ASYNC = "async"
    DEFER = "defer"
    IDLE = "idle"
    INTERACTION = "interaction"
    MANUAL = "manual"


HEAD_STRATEGIES: Final = frozenset({Strategy.BLOCKING, Strategy.ASYNC, Strategy.DEFER})
"""The strategies a `<script>` tag in the head can carry out."""

SCRIPT_ATTR: Final = "data-next-script"
"""The attribute every script the framework writes names its script by."""

ALLOWED_ATTRS: Final = frozenset({"integrity", "crossorigin", "referrerpolicy"})
"""The attributes a script may carry beside the `data-*` ones."""

_DATA_ATTR: Final = re.compile(r"data-[a-z0-9][a-z0-9._-]*")


def allowed_attr(name: object) -> bool:
    """Whether a script may carry the attribute `name`."""
    if not isinstance(name, str):
        return False
    if name in ALLOWED_ATTRS:
        return True
    return name != SCRIPT_ATTR and _DATA_ATTR.fullmatch(name) is not None


@dataclass(frozen=True, slots=True)
class Script:
    """One third-party script. Its `init` body runs before its `src` loads."""

    name: str
    src: str | None = None
    init: str | None = None
    strategy: Strategy = Strategy.ASYNC
    category: str = NECESSARY
    auto: bool = True
    attrs: Mapping[str, str] = field(default_factory=dict)

    @property
    def gated(self) -> bool:
        """Whether the script waits for a category the visitor has to grant."""
        return self.category != NECESSARY

    def allowed_attrs(self) -> dict[str, str]:
        """Return the attributes the script may carry, in declaration order."""
        return {
            name: value
            for name, value in self.attrs.items()
            if allowed_attr(name) and isinstance(value, str)
        }


__all__ = [
    "ALLOWED_ATTRS",
    "HEAD_STRATEGIES",
    "SCRIPT_ATTR",
    "Script",
    "Strategy",
    "allowed_attr",
]
