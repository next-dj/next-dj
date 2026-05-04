from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from next.static import StaticFilesBackend


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest


class BabelStaticBackend(StaticFilesBackend):
    """Render JSX assets as Babel-standalone scripts and apply CDN cache attrs.

    The framework selects the renderer method by name through the
    public `KindRegistry`, so pairing the `jsx` kind with the
    `render_babel_script_tag` method is enough to teach the pipeline
    about a new asset type without touching any core code. The same
    backend also reads `OPTIONS.SCRIPT_CACHE_ATTRS` and decorates
    external (`http://` or `https://`) script URLs with cache-friendly
    attributes such as `crossorigin="anonymous"` so the browser can
    reuse a single cached copy of React, ReactDOM, and Babel across
    pages.
    """

    _BABEL_TAG = '<script type="text/babel" src="{url}"{attrs}></script>'
    _SCRIPT_TAG = '<script src="{url}"{attrs}></script>'

    _DEFAULT_CDN_ATTRS: ClassVar[dict[str, str]] = {
        "crossorigin": "anonymous",
        "referrerpolicy": "no-referrer",
    }

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Pull the optional cache-attrs override from `OPTIONS`."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        attrs = opts.get("SCRIPT_CACHE_ATTRS")
        if attrs is None:
            attrs = self._DEFAULT_CDN_ATTRS
        self._cdn_attrs = dict(attrs)

    def render_babel_script_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,  # noqa: ARG002
    ) -> str:
        """Return a `text/babel` script tag for a JSX asset URL."""
        return self._BABEL_TAG.format(url=url, attrs=self._extra_attrs(url))

    def render_script_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,  # noqa: ARG002
    ) -> str:
        """Render external scripts with cache attrs and locals untouched."""
        attrs = self._extra_attrs(url)
        if not attrs:
            return super().render_script_tag(url)
        return self._SCRIPT_TAG.format(url=url, attrs=attrs)

    def _extra_attrs(self, url: str) -> str:
        """Return the formatted attribute suffix for external URLs only."""
        if not self._is_external(url):
            return ""
        return "".join(f' {k}="{v}"' for k, v in self._cdn_attrs.items())

    @staticmethod
    def _is_external(url: str) -> bool:
        return url.startswith(("http://", "https://"))
