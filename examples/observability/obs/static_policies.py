"""Custom dedup strategy that records every filtered duplicate.

`InstrumentedDedup` reuses the URL-based dedup logic of the framework
default and bumps a counter every time a key collides with one
already in the collector. Plugged in through
`DEFAULT_STATIC_BACKENDS[0]["OPTIONS"]["DEDUP_STRATEGY"]` in
`config/settings.py`.
"""

from collections.abc import Hashable

from next.static import StaticAsset
from next.static.collector import UrlDedup

from .metrics import incr


class InstrumentedDedup(UrlDedup):
    """`UrlDedup` that counts dedup-key generations per asset kind."""

    def __init__(self) -> None:
        """Track which keys were already seen during this collector lifetime."""
        self._seen: set[Hashable] = set()

    def key(self, asset: StaticAsset) -> Hashable:
        """Return the parent key and bump the per-kind counter on a hit."""
        result = super().key(asset)
        if result in self._seen:
            incr("static.dedup", str(asset.kind))
        else:
            self._seen.add(result)
        incr("static.asset", str(asset.kind))
        return result
