from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from django.http import HttpRequest
from django.utils.html import escape, format_html

from next.components import FileComponentsBackend
from next.static import StaticFilesBackend

from .metrics import incr


if TYPE_CHECKING:
    from collections.abc import Mapping

    from next.components import ComponentInfo


class CountingComponentsBackend(FileComponentsBackend):
    """`FileComponentsBackend` that counts component resolutions."""

    def get_component(self, name: str, template_path: Path) -> "ComponentInfo | None":
        """Return the component info and record one lookup event."""
        info = super().get_component(name, template_path)
        if info is not None:
            incr("components.lookup", name)
        return info


class BabelJsxBackend(StaticFilesBackend):
    """`StaticFilesBackend` that renders `.jsx` as `<script type="text/babel">`.

    A `babel_tag` option overrides the tag template like `css_tag` and `js_tag`.
    """

    _DEFAULT_BABEL_TAG: ClassVar[str] = (
        '<script type="text/babel" data-presets="env,react" src="{url}"{nonce_attr}>'
        "</script>"
    )

    def __init__(self, config: "Mapping[str, Any] | None" = None) -> None:
        """Read the `babel_tag` option in addition to parent options."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        self._babel_tag = str(opts.get("babel_tag") or self._DEFAULT_BABEL_TAG)

    def render_babel_script_tag(
        self, url: str, *, request: HttpRequest | None = None, nonce: str | None = None
    ) -> str:
        """Return a `<script type="text/babel">` tag pointing at `url`.

        Babel-standalone copies the nonce onto the compiled script it injects.
        """
        del request
        nonce_attr = format_html(' nonce="{}"', nonce) if nonce else ""
        return self._babel_tag.format(url=escape(url), nonce_attr=nonce_attr)
