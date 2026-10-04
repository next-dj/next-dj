"""Pluggable backend contract and Django-staticfiles default implementation.

The default backend resolves URLs through Django staticfiles,
so manifest, S3, and CDN settings apply automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from html import escape
from typing import TYPE_CHECKING, Any, ClassVar, Final, override

from django.contrib.staticfiles.storage import staticfiles_storage

from next.caches import BoundedCache

from .assets import StaticNamespace, static_name
from .errors import StaticAssetNotFoundError
from .runtime import TAG_FIELDS, nonce_attr, usable_template


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest


# Changing one of these rebuilds `staticfiles_storage`, so every URL resolved through
# the previous storage refers to a manifest that no longer applies.
MANIFEST_SETTINGS = frozenset({"STATIC_ROOT", "STATIC_URL", "STORAGES"})

DEFAULT_STATIC_BACKEND: Final = "next.static.StaticFilesBackend"
"""The backend a `STATIC_BACKENDS` entry without `BACKEND` names, and the fallback."""


class StaticBackend(ABC):
    """Pluggable strategy for resolving asset files to URLs and rendering tags.

    The base class memoises what each backend resolves, so `forget_urls` exists for
    the framework to tell it when the storage behind those URLs is rebuilt.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Store the raw config mapping and prime the URL memo it may fill."""
        self._config: Mapping[str, Any] = config or {}
        # Bounded only to cap memory for an unbounded set of names. Eviction is by
        # insertion order, so a cache hit reorders nothing.
        self._url_cache: BoundedCache[tuple[str, ...], str] = BoundedCache()

    @property
    def config(self) -> Mapping[str, Any]:
        """Return the backend entry supplied at construction time."""
        return self._config

    def asset_url(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return the public URL of an already-resolved asset for this render.

        Override it to apply a per-request scheme, such as a per-tenant prefix, to
        every asset URL, the `next.min.js` runtime tag included.
        """
        del request
        return url

    def resolve_url(self, reference: str) -> str:
        """Turn an authored asset reference into a public URL.

        The default returns the reference unchanged.
        """
        return reference

    def forget_urls(self) -> None:
        """Drop every memoised URL, so the next lookup resolves it again.

        `StaticManager.forget_backend_urls` calls it after a storage rebuild and drops
        the other memoised URLs itself, so call that method rather than this hook.
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

    `css_tag`, `js_tag`, and `module_tag` hold format strings taking `{url}` and
    `{nonce_attr}`. Both are HTML-escaped, since the tag is inserted after the template
    engine has rendered.
    """

    _DEFAULT_CSS_TAG: ClassVar[str] = '<link rel="stylesheet" href="{url}"{nonce_attr}>'
    _DEFAULT_JS_TAG: ClassVar[str] = '<script src="{url}"{nonce_attr}></script>'
    _DEFAULT_MODULE_TAG: ClassVar[str] = (
        '<script type="module" src="{url}"{nonce_attr}></script>'
    )

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Read the tag templates from `OPTIONS`, the default replacing a broken one."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        self._css_tag = self._template(opts, "css_tag", self._DEFAULT_CSS_TAG)
        self._js_tag = self._template(opts, "js_tag", self._DEFAULT_JS_TAG)
        self._module_tag = self._template(opts, "module_tag", self._DEFAULT_MODULE_TAG)

    def _template(self, opts: Mapping[str, Any], key: str, default: str) -> str:
        where = f"STATIC_BACKENDS OPTIONS[{key!r}] of {type(self).__name__}"
        return usable_template(
            str(opts.get(key) or default), default, TAG_FIELDS, where
        )

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

        The memo is read before the reference is classified, so a ready URL is parsed
        once rather than on every render, and `forget_urls` drops both answers.
        """
        cache_key = ("name", reference)
        cached = self._url_cache.get(cache_key)
        if cached is not None:
            return cached
        name = static_name(reference)
        if name is None:
            self._url_cache[cache_key] = reference
            return reference
        try:
            url = str(staticfiles_storage.url(name))
        except ValueError as e:
            raise StaticAssetNotFoundError(name) from e
        self._url_cache[cache_key] = url
        return url

    def render_link_tag(
        self, url: str, *, request: HttpRequest | None = None, nonce: str | None = None
    ) -> str:
        """Return a link tag built from the configured css_tag template.

        The `request` argument holds the contract and the default backend ignores it.
        """
        del request
        return self._css_tag.format(url=escape(str(url)), nonce_attr=nonce_attr(nonce))

    def render_script_tag(
        self, url: str, *, request: HttpRequest | None = None, nonce: str | None = None
    ) -> str:
        """Return a script tag built from the configured js_tag template.

        The `request` argument holds the contract and the default backend ignores it.
        """
        del request
        return self._js_tag.format(url=escape(str(url)), nonce_attr=nonce_attr(nonce))

    def render_module_tag(
        self, url: str, *, request: HttpRequest | None = None, nonce: str | None = None
    ) -> str:
        """Return a module script tag built from the configured module_tag template.

        The `request` argument holds the contract and the default backend ignores it.
        """
        del request
        return self._module_tag.format(
            url=escape(str(url)), nonce_attr=nonce_attr(nonce)
        )
