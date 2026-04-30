"""Pluggable backend contract and Django-staticfiles default implementation.

A static backend turns co-located asset paths into public URLs and
renders them as `<link>` or `<script>` tags. The default backend
delegates URL resolution to Django staticfiles, so manifest hashing, S3
storage, and CDN configuration from Django settings apply
automatically.

A small factory builds backend instances from
`NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS']` entries. The factory also
emits the `backend_loaded` signal so user code may react to backend
construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib.staticfiles.storage import staticfiles_storage

from next.conf import import_class_cached

from .assets import StaticNamespace, default_kinds
from .signals import backend_loaded


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest


class StaticBackend(ABC):
    """Pluggable strategy for resolving asset files to URLs and rendering tags.

    The constructor accepts the full backend entry from
    `DEFAULT_STATIC_BACKENDS`, which has the shape
    `{"BACKEND": "...", "OPTIONS": {...}}`. The base class stores the
    mapping on the `config` property. Subclasses are free to read any
    keys they expose to users.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Store the raw config mapping for subclasses to read."""
        self._config: Mapping[str, Any] = config or {}

    @property
    def config(self) -> Mapping[str, Any]:
        """Return the backend entry supplied at construction time."""
        return self._config

    @abstractmethod
    def register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        """Register a co-located asset file and return its public URL.

        The `source_path` argument is the absolute path to the source
        file on disk. The `logical_name` argument is the path without an
        extension, for example `"about"` or `"components/card"`. The
        `kind` argument must be a kind registered in the default kind
        registry. The method raises `RuntimeError` when the asset
        cannot be resolved to a URL.
        """

    @abstractmethod
    def render_link_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return an HTML link tag for a CSS asset URL.

        The `request` keyword argument is the active `HttpRequest` when
        the tag is rendered as part of a page response. It is `None`
        for renders that happen outside a request lifecycle. Subclasses
        may use it to rewrite the URL based on per-request state. The
        default backend ignores it.
        """

    @abstractmethod
    def render_script_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return an HTML script tag for a JS asset URL.

        The `request` keyword argument follows the same contract as
        `render_link_tag`. The default backend ignores it.
        """


class StaticFilesBackend(StaticBackend):
    """Resolve co-located asset URLs through Django staticfiles.

    Assets live in the `next/` staticfiles namespace so manifest
    storage, S3 storage, and CDN settings apply automatically.

    Two option keys are recognised in the backend entry. The
    `css_tag` key sets a format string for `<link>` tags. It must
    contain the `{url}` placeholder. Extra attributes such as
    `crossorigin` or `integrity` can be baked directly into the
    template. The `js_tag` key sets a format string for `<script>`
    tags. The same placeholder rules apply. Attributes such as `defer`
    or `async` are added by writing them into the template.
    """

    _DEFAULT_CSS_TAG: ClassVar[str] = '<link rel="stylesheet" href="{url}">'
    _DEFAULT_JS_TAG: ClassVar[str] = '<script src="{url}"></script>'

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Read tag templates from the OPTIONS mapping and prime caches."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        self._css_tag = str(opts.get("css_tag") or self._DEFAULT_CSS_TAG)
        self._js_tag = str(opts.get("js_tag") or self._DEFAULT_JS_TAG)
        self._url_cache: dict[str, str] = {}

    def _logical_static_path(self, logical_name: str, kind: str) -> str:
        extension = default_kinds.extension(kind)
        return f"{StaticNamespace.NEXT}/{logical_name}{extension}"

    def register_file(
        self,
        source_path: Path,  # noqa: ARG002
        logical_name: str,
        kind: str,
    ) -> str:
        """Return the staticfiles URL for `next/<logical_name>.<ext>`.

        The result is cached per logical path. Missing entries in the
        staticfiles manifest are reported as `RuntimeError` with a hint
        about running `collectstatic`.
        """
        path = self._logical_static_path(logical_name, kind)
        cached = self._url_cache.get(path)
        if cached is not None:
            return cached
        try:
            url = str(staticfiles_storage.url(path))
        except ValueError as e:
            msg = (
                f"Static asset {path!r} is missing from Django staticfiles "
                "manifest. Run collectstatic and ensure the next static "
                "finder is enabled."
            )
            raise RuntimeError(msg) from e
        self._url_cache[path] = url
        return url

    def render_link_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,  # noqa: ARG002
    ) -> str:
        """Return a link tag built from the configured css_tag template.

        The `request` argument is accepted for contract compatibility
        and ignored by the default backend.
        """
        return self._css_tag.format(url=url)

    def render_script_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,  # noqa: ARG002
    ) -> str:
        """Return a script tag built from the configured js_tag template.

        The `request` argument is accepted for contract compatibility
        and ignored by the default backend.
        """
        return self._js_tag.format(url=url)


class StaticsFactory:
    """Build static backend instances from configuration dicts."""

    DEFAULT_BACKEND: ClassVar[str] = "next.static.StaticFilesBackend"

    @classmethod
    def create_backend(cls, config: Mapping[str, Any]) -> StaticBackend:
        """Instantiate the backend class named by `config['BACKEND']`.

        Raises `TypeError` when the configured class is not a subclass
        of `StaticBackend`.
        """
        backend_path = config.get("BACKEND", cls.DEFAULT_BACKEND)
        backend_class = import_class_cached(backend_path)
        if not isinstance(backend_class, type) or not issubclass(
            backend_class, StaticBackend
        ):
            msg = f"Backend {backend_path!r} is not a StaticBackend subclass"
            raise TypeError(msg)
        instance: StaticBackend = backend_class(config)
        backend_loaded.send(
            sender=backend_class, config=dict(config), instance=instance
        )
        return instance
