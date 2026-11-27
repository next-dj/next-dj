"""The third-party scripts provider every page head and init payload reads."""

from typing import Final

from next.ports import ScriptRender, ScriptShare
from next.static.runtime import CHUNK_PAYLOAD_KEYS

from .manager import scripts_manager


PRIORITY: Final = 500
"""The rank of the scripts among the page script providers, the last ones."""


class PageScriptsImpl:
    """Binds the provider to `scripts_manager.render`."""

    name: Final = "scripts"
    priority: Final = PRIORITY
    keys: Final = CHUNK_PAYLOAD_KEYS

    def applies(self) -> bool:
        """Return true, as the scripts of a page follow its directory."""
        return True

    def share(self, render: ScriptRender, /) -> ScriptShare | None:
        """Return the head tags and the payload entries of one render, `None` for none.

        A provider later in rank writes its head tags after these.
        """
        head, entries = scripts_manager.render(
            render.collector,
            page_path=render.page_path,
            request=render.request,
            nonce=render.nonce,
        )
        if not (head or entries):
            return None
        return ScriptShare(head=head, entries=entries)


__all__ = ["PageScriptsImpl"]
