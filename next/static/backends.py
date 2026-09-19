"""Pluggable backend contract and Django-staticfiles default implementation.

The default backend resolves URLs through Django staticfiles,
so manifest, S3, and CDN settings apply automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, override

from django.contrib.staticfiles.storage import staticfiles_storage

from next.caches import BoundedCache

from .assets import StaticNamespace, is_static_name
from .errors import StaticAssetNotFoundError


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest


# Changing one of these rebuilds `staticfiles_storage`, so every URL resolved
# through the manifest it held answers for a manifest that is gone.
MANIFEST_SETTINGS = frozenset({"STATIC_ROOT", "STATIC_URL", "STORAGES"})


class StaticBackend(ABC):
    """Pluggable strategy for resolving asset files to URLs and rendering tags.

    The base class memoises what each backend resolves, so `forget_urls` exists for
    the framework to tell it when the storage behind those URLs is rebuilt.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Store the raw config mapping and prime the URL memo it may fill."""
        self._config: Mapping[str, Any] = config or {}
        # Bounded against a backend asked for logical names without end rather than
        # as a policy, so the stalest insert goes and a warm tag reorders nothing.
        self._url_cache: BoundedCache[tuple[str, ...], str] = BoundedCache()

    @property
    def config(self) -> Mapping[str, Any]:
        """Return the backend entry supplied at construction time."""
        return self._config

    def asset_url(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return the public URL of an already-resolved asset for this render.

        Overriding this one hook lets a per-request scheme, such as a per-tenant
        prefix, reach even the framework-built `next.min.js` runtime tag.
        """
        del request
        return url

    def resolve_url(self, reference: str) -> str:
        """Turn an authored asset reference into a public URL.

        The default keeps the reference literal, so an older backend renders as before.
        """
        return reference

    def forget_urls(self) -> None:
        """Drop every memoised URL, so the next lookup resolves it again.

        A backend that remembers resolved URLs outside the base memo overrides this
        hook, so a storage rebuild reaches it regardless of the memo's shape.
        """
        self._url_cache.clear()

    @abstractmethod
    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Register a co-located asset file and return its public URL.

        Discovery asks on every render rather than caching the answer, so a backend
        may resolve the same file to a different URL per request.
        """


class StaticFilesBackend(StaticBackend):
    """Resolve co-located asset URLs through Django staticfiles.

    `css_tag`, `js_tag`, and `module_tag` hold format strings needing `{url}`.
    """

    _DEFAULT_CSS_TAG: ClassVar[str] = '<link rel="stylesheet" href="{url}">'
    _DEFAULT_JS_TAG: ClassVar[str] = '<script src="{url}"></script>'
    _DEFAULT_MODULE_TAG: ClassVar[str] = '<script type="module" src="{url}"></script>'

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Read the tag templates from the OPTIONS mapping."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        self._css_tag = str(opts.get("css_tag") or self._DEFAULT_CSS_TAG)
        self._js_tag = str(opts.get("js_tag") or self._DEFAULT_JS_TAG)
        self._module_tag = str(opts.get("module_tag") or self._DEFAULT_MODULE_TAG)

    def _logical_static_path(self, logical_name: str, suffix: str) -> str:
        return f"{StaticNamespace.NEXT}/{logical_name}{suffix}"

    @override
    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Return the staticfiles URL for `next/<logical_name><suffix>`.

        Memoised per `(logical_name, suffix)`, because staticfiles reads its manifest
        once per process, and `forget_urls` drops it when that manifest moves.
        """
        del kind
        suffix = source_path.suffix
        cache_key = ("file", logical_name, suffix)
        cached = self._url_cache.get(cache_key)
        if cached is not None:
            return cached
        path = self._logical_static_path(logical_name, suffix)
        try:
            url = str(staticfiles_storage.url(path))
        except ValueError as e:
            raise StaticAssetNotFoundError(path) from e
        self._url_cache[cache_key] = url
        return url

    @override
    def resolve_url(self, reference: str) -> str:
        """Resolve a staticfiles name through storage and leave a ready URL alone.

        Memoised in the shared URL memo, so `forget_urls` drops it with the file space.
        """
        if not is_static_name(reference):
            return reference
        cache_key = ("name", reference)
        cached = self._url_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            url = str(staticfiles_storage.url(reference))
        except ValueError as e:
            raise StaticAssetNotFoundError(reference) from e
        self._url_cache[cache_key] = url
        return url

    def render_link_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return a link tag built from the configured css_tag template.

        The `request` argument holds the contract and the default backend ignores it.
        """
        del request
        return self._css_tag.format(url=url)

    def render_script_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return a script tag built from the configured js_tag template.

        The `request` argument holds the contract and the default backend ignores it.
        """
        del request
        return self._js_tag.format(url=url)

    def render_module_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return a module script tag built from the configured module_tag template.

        The `request` argument holds the contract and the default backend ignores it.
        """
        del request
        return self._module_tag.format(url=url)
