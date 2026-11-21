"""Record the lazy bundles a render needs, which its page then declares by tag."""

from __future__ import annotations

from django.template import Context

from next.seeding import COLLECTOR_KEY

from .collector import StaticCollector


def require_bundle(
    target: Context | StaticCollector | None, name: str, *, urgent: bool = False
) -> None:
    """Record on the collector of `target` that the render needs the bundle `name`.

    A render without a collector, such as a bare template render, records nothing.
    """
    collector = target.get(COLLECTOR_KEY) if isinstance(target, Context) else target
    if isinstance(collector, StaticCollector):
        collector.require(name, urgent=urgent)


__all__ = ["require_bundle"]
