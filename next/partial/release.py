"""The asset version and wire protocol every runtime page asserts, as a provider."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from next.caches import pure_cache
from next.ports import ScriptShare
from next.static.runtime import PROTOCOL_PAYLOAD_KEY, VERSION_PAYLOAD_KEY, payload_entry

from .manager import asset_version
from .stream import PROTOCOL


if TYPE_CHECKING:
    from next.ports import ScriptRender


PRIORITY: Final = 100
"""The rank of the release among the page script providers, the first entries."""


@pure_cache(4)
def _released(version: str) -> ScriptShare:
    """Return the `$version` and `$protocol` entries of one asset version."""
    return ScriptShare(
        shared=(
            f"{payload_entry(VERSION_PAYLOAD_KEY, json.dumps(version))},"
            f"{payload_entry(PROTOCOL_PAYLOAD_KEY, str(PROTOCOL))}"
        )
    )


class ReleaseScripts:
    """The release a runtime page was built for, which a runtime of another refuses."""

    name: Final = "release"
    priority: Final = PRIORITY
    keys: Final = frozenset({VERSION_PAYLOAD_KEY, PROTOCOL_PAYLOAD_KEY})

    def applies(self) -> bool:
        """Return true, as every runtime page names its release."""
        return True

    def share(self, _render: ScriptRender, /) -> ScriptShare:
        """Return the entries naming the asset version and the wire protocol."""
        return _released(asset_version())


__all__ = ["ReleaseScripts"]
