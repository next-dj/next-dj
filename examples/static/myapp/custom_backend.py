"""Custom :class:`StaticBackend` demonstrating ``OPTIONS`` extensibility.

The backend subclasses :class:`next.static.StaticFilesBackend` and reads a
few extra attribute knobs from its ``OPTIONS`` mapping. Values flow through
``NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS']`` so templates themselves remain
unchanged. The parent class already honors ``css_tag`` / ``js_tag`` format
strings, but overriding :meth:`render_script_tag` shows how to derive an
attribute set programmatically — useful when integrity hashes or defer flags
depend on runtime state rather than a static format string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from next.static import StaticFilesBackend


if TYPE_CHECKING:
    from collections.abc import Mapping


class AttributedStaticFilesBackend(StaticFilesBackend):
    """Backend that appends configurable attributes to every ``<script>`` tag.

    ``OPTIONS`` keys consumed by this backend:

    ``defer`` (``bool``)
        Emit the ``defer`` attribute on every script tag.

    ``crossorigin`` (``str``)
        Value for the ``crossorigin`` attribute (e.g. ``"anonymous"``).

    ``integrity`` (``Mapping[str, str]``)
        Map of logical URL to subresource-integrity hash. When a rendered
        URL is in the mapping the corresponding ``integrity`` attribute is
        added to the tag.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Read ``defer``/``crossorigin``/``integrity`` knobs from ``OPTIONS``."""
        super().__init__(config)
        opts = dict((config or {}).get("OPTIONS") or {})
        self._defer = bool(opts.get("defer", False))
        self._crossorigin = opts.get("crossorigin") or None
        integrity = opts.get("integrity") or {}
        self._integrity: dict[str, str] = {str(k): str(v) for k, v in integrity.items()}

    def render_script_tag(self, url: str) -> str:
        """Return a ``<script>`` tag enriched with configured attributes."""
        attrs: list[str] = [f'src="{url}"']
        if self._defer:
            attrs.append("defer")
        if self._crossorigin is not None:
            attrs.append(f'crossorigin="{self._crossorigin}"')
        sri = self._integrity.get(url)
        if sri is not None:
            attrs.append(f'integrity="{sri}"')
        return f"<script {' '.join(attrs)}></script>"
