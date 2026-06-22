from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from django.http import HttpRequest

from next.components import FileComponentsBackend
from next.static import StaticFilesBackend

from .metrics import incr


if TYPE_CHECKING:
    from collections.abc import Mapping

    from next.components.info import ComponentInfo


class CountingComponentsBackend(FileComponentsBackend):
    """`FileComponentsBackend` that counts component resolutions."""

    def get_component(
        self,
        name: str,
        template_path: Path,
    ) -> "ComponentInfo | None":
        """Return the component info and record one lookup event."""
        info = super().get_component(name, template_path)
        if info is not None:
            incr("components.lookup", name)
        return info


class BabelJsxBackend(StaticFilesBackend):
    """`StaticFilesBackend` that renders `.jsx` as `<script type="text/babel">`.

    The kind itself is registered in `apps.py` so the framework picks
    up `component.jsx` next to `component.djx` automatically. The
    `babel_tag` option lets users override the tag template the same
    way `css_tag` and `js_tag` do on the parent.
    """

    _DEFAULT_BABEL_TAG: ClassVar[str] = (
        '<script type="text/babel" data-presets="env,react" src="{url}"></script>'
    )

    def __init__(self, config: "Mapping[str, Any] | None" = None) -> None:
        """Read the `babel_tag` option in addition to parent options."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        self._babel_tag = str(opts.get("babel_tag") or self._DEFAULT_BABEL_TAG)

    def render_babel_script_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,
    ) -> str:
        """Return a `<script type="text/babel">` tag pointing at `url`."""
        return self._babel_tag.format(url=url)
