"""Pluggable backend contract and Django-staticfiles default implementation.

A static backend turns co-located asset paths into public URLs and renders them through
one of its named renderer methods. The default backend delegates URL resolution to
Django staticfiles, so manifest hashing, S3 storage, and CDN configuration from Django
settings apply automatically.

The abstract `StaticBackend` only mandates `register_file`. Renderer methods are
concrete on the default backend and selected per asset by `KindRegistry.renderer(kind)`.
Custom backends extend the surface by adding more named methods such as
`render_babel_script_tag` and registering kinds that point to them.

Instances are built from `NEXT_FRAMEWORK['STATIC_BACKENDS']`
entries by the static manager, which emits the `backend_loaded`
signal for each one so user code may react to backend construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, override

from django.contrib.staticfiles.storage import staticfiles_storage

from .assets import StaticNamespace


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest


class StaticBackend(ABC):
    """Pluggable strategy for resolving asset files to URLs and rendering tags.

    The constructor accepts the full backend entry from `STATIC_BACKENDS`, which has the
    shape `{"BACKEND": "...", "OPTIONS": {...}}`. The base class stores the mapping on
    the `config` property. Subclasses are free to read any keys they expose to users.

    The only abstract requirement is `register_file`. Renderer
    methods are added by subclasses and selected per asset through
    `KindRegistry.renderer(kind)`. The default backend below ships
    `render_link_tag` and `render_script_tag` for the built-in `css` and `js`
    kinds. Custom backends register additional kinds and expose matching methods.
    """

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Store the raw config mapping for subclasses to read."""
        self._config: Mapping[str, Any] = config or {}

    @property
    def config(self) -> Mapping[str, Any]:
        """Return the backend entry supplied at construction time."""
        return self._config

    @abstractmethod
    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Register a co-located asset file and return its public URL.

        The `source_path` argument is the absolute path to the source file on
        disk. The `logical_name` argument is the path without an extension,
        for example `"about"` or `"components/card"`. The `kind` argument
        must be a kind registered in the default kind registry. The method
        raises `RuntimeError` when the asset cannot be resolved to a URL.
        Discovery asks on every render rather than remembering the answer, so a
        backend is free to resolve the same file to a different URL per request.
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
    _DEFAULT_MODULE_TAG: ClassVar[str] = '<script type="module" src="{url}"></script>'

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Read tag templates from the OPTIONS mapping and prime caches."""
        super().__init__(config)
        opts = dict(self._config.get("OPTIONS") or {})
        self._css_tag = str(opts.get("css_tag") or self._DEFAULT_CSS_TAG)
        self._js_tag = str(opts.get("js_tag") or self._DEFAULT_JS_TAG)
        self._module_tag = str(opts.get("module_tag") or self._DEFAULT_MODULE_TAG)
        self._url_cache: dict[tuple[str, str], str] = {}

    def _logical_static_path(self, logical_name: str, suffix: str) -> str:
        return f"{StaticNamespace.NEXT}/{logical_name}{suffix}"

    @override
    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Return the staticfiles URL for `next/<logical_name><suffix>`.

        The suffix is taken from `source_path.suffix`, so a single kind
        can serve multiple file extensions if a custom backend wishes to.
        The answer is memoised per `(logical_name, suffix)` for the life of
        this backend, which `StaticManager.reload()` ends, because staticfiles
        itself reads its manifest once per process. Missing entries in that
        manifest are reported as `RuntimeError` with a hint about running
        `collectstatic`. The `kind` argument is part of the contract and
        ignored here, the suffix alone names the file.
        """
        del kind
        suffix = source_path.suffix
        cache_key = (logical_name, suffix)
        cached = self._url_cache.get(cache_key)
        if cached is not None:
            return cached
        path = self._logical_static_path(logical_name, suffix)
        try:
            url = str(staticfiles_storage.url(path))
        except ValueError as e:
            msg = (
                f"Static asset {path!r} is missing from Django staticfiles "
                "manifest. Run collectstatic and ensure the next static "
                "finder is enabled."
            )
            raise RuntimeError(msg) from e
        self._url_cache[cache_key] = url
        return url

    def render_link_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return a link tag built from the configured css_tag template.

        The `request` argument is accepted for contract compatibility
        and ignored by the default backend.
        """
        del request
        return self._css_tag.format(url=url)

    def render_script_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return a script tag built from the configured js_tag template.

        The `request` argument is accepted for contract compatibility
        and ignored by the default backend.
        """
        del request
        return self._js_tag.format(url=url)

    def render_module_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Return a module script tag built from the configured module_tag template.

        The `request` argument is accepted for contract compatibility
        and ignored by the default backend.
        """
        del request
        return self._module_tag.format(url=url)
