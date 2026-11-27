"""The typed `page` calls a `page.py` declares with, each handed to its registration.

The router call is read by name, as the feature that owns it sits above the pages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.introspect import registering_site
from next.pages.compression import compress_declaration
from next.pages.declarations import page_declarations
from next.pages.defer import defer_declaration
from next.pages.transactions import transaction_declaration


if TYPE_CHECKING:
    from next.transactions import AtomicMode
    from next.transactions.policy import AliasChoice


class DeclaringCalls:
    """The `page.transaction`, `defer`, `compress` and `router` calls of the manager."""

    def transaction(
        self, *, atomic: AtomicMode, aliases: AliasChoice | list[str] = None
    ) -> None:
        """Run this page in a transaction, or out of one, whatever the request method.

        `"writes"` opens it before the first write, `aliases=None` reads `ALIASES`.
        """
        transaction_declaration.declare(
            registering_site(), atomic=atomic, aliases=aliases
        )

    def defer(
        self, *, timeout: float | str | None = None, parallel: bool = True
    ) -> None:
        """Bound the streamed body of this page by `timeout` and place its job.

        `False` keeps the job on the request lane, `True` lets it take the pool.
        """
        site = registering_site()
        defer_declaration.declare(site, timeout=timeout, parallel=parallel)

    def compress(self, *, enabled: bool) -> None:
        """Let next.dj compress this page with `True`, or keep it plain with `False`.

        A page that renders a secret beside reflected input opts out against BREACH.
        """
        compress_declaration.declare(registering_site(), enabled=enabled)

    def router(
        self, *, navigate: bool = True, prefetch: bool = True, snapshot: bool = True
    ) -> None:
        """Declare how the client router treats this page.

        A page whose context callables act on a visit opts out of prefetch.
        """
        page_declarations.named("router").declare(
            registering_site(), navigate=navigate, prefetch=prefetch, snapshot=snapshot
        )


__all__ = ["DeclaringCalls"]
