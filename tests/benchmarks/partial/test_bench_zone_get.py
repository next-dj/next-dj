from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings
from django.urls import resolve

from next.pages.loaders import load_python_module_memo
from next.pages.manager import page as page_singleton
from tests.benchmarks.factories import build_layout_page
from tests.support import partial_meta, plain_get, routed, write_page
from tests.support.access import PAGE_HEAD


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from django.http import HttpRequest, HttpResponseBase


_ZONE_COUNT = 10
_ZONED_TEMPLATE = (
    "<main>"
    + "".join(
        f'{{% zone "z_{i}" %}}<p>{{{{ k_{i} }}}}</p>{{% endzone %}}'
        for i in range(_ZONE_COUNT)
    )
    + "</main>"
)
_BATCH = ",".join(f"z_{i}" for i in range(5))
_MANIFEST_ENTRIES = 200


def _zone_get(zones: str) -> HttpRequest:
    """Return the GET a client polling `zones` sends."""
    request = plain_get("/")
    request.META.update(partial_meta(zones=zones))
    return request


def _cheap_provider() -> str:
    """Context provider of negligible cost, so the tick overhead stays visible."""
    return "v"


@contextmanager
def _zone_view(
    directory: Path, *, providers: int = 0
) -> Iterator[Callable[..., HttpResponseBase]]:
    """Build the unified view of a ten-zone page with `providers` zone-gated callables.

    Each provider is bound to its own zone, so a tick runs one and filters the rest.
    """
    page_file = build_layout_page(directory, layouts=2, template=_ZONED_TEMPLATE)
    registry = page_singleton._context_manager
    for i in range(providers):
        registry.register_context(page_file, f"k_{i}", _cheap_provider, zone=f"z_{i}")
    module = load_python_module_memo(page_file)
    view = page_singleton._create_unified_view(page_file, module)
    try:
        yield view
    finally:
        registry._context_registry.pop(page_file, None)
        page_singleton.clear_template_caches()


@contextmanager
def _manifest_storage(root: Path) -> Iterator[None]:
    """Point staticfiles at a manifest storage carrying an unhashed manifest.

    An empty recorded hash sends `asset_version` down the hashing branch.
    """
    static_root = root / "static_root"
    static_root.mkdir()
    paths = {f"a/{i}.css": f"a/{i}.{i:012x}.css" for i in range(_MANIFEST_ENTRIES)}
    (static_root / "staticfiles.json").write_text(
        json.dumps({"version": "1.1", "hash": "", "paths": paths})
    )
    storages = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
        },
    }
    with override_settings(STATIC_ROOT=str(static_root), STORAGES=storages):
        yield


class TestBenchZoneGetThroughView:
    """Sweep of the zone-GET tick as a client makes it, through the unified view."""

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_warm_tick(self, tmp_path: Path, benchmark) -> None:
        request = _zone_get("z_0")
        with _zone_view(tmp_path) as view:
            view(request)
            benchmark(view, request)

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_batch_of_five(self, tmp_path: Path, benchmark) -> None:
        """Five zones asked for in one tick, against the single-zone tick."""
        request = _zone_get(_BATCH)
        with _zone_view(tmp_path) as view:
            view(request)
            benchmark(view, request)

    @pytest.mark.parametrize("providers", [1, 10], ids=["one", "ten"])
    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_zone_gated_providers(
        self, tmp_path: Path, providers: int, benchmark
    ) -> None:
        """Tick cost against the number of zone-gated callables the page declares."""
        request = _zone_get("z_0")
        with _zone_view(tmp_path, providers=providers) as view:
            view(request)
            benchmark(view, request)

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_manifest_storage(self, tmp_path: Path, benchmark) -> None:
        """The same warm tick with a manifest staticfiles storage configured."""
        request = _zone_get("z_0")
        with _manifest_storage(tmp_path), _zone_view(tmp_path) as view:
            view(request)
            benchmark(view, request)


_PEEK = '{% zone "peek" %}<h2>peek {{ pk }}</h2>{% endzone %}'
_PHOTO = "<p>photo {{ pk }}</p>"
_PEEK_META = (
    'metadata = {"title": "Peek", "description": "A peek", "canonical": True}\n'
)


def _photo_site(
    root: Path, *, target: str = _PHOTO, intercept: str | None = None, meta: str = ""
) -> None:
    """Write the target `photo/[int:pk]` and, if given, the intercept over it."""
    write_page(root, "photo/[int:pk]", PAGE_HEAD, body=target)
    if intercept is not None:
        write_page(root, "(.)photo/[int:pk]", f"{PAGE_HEAD}{meta}", body=intercept)


class TestBenchInterceptZoneGet:
    """A zone GET on a target no rule covers, against one a rule serves."""

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_target_without_intercept(self, tmp_path: Path, benchmark) -> None:
        """The miss path, the target serves its own zone and no rule is looked up."""
        _photo_site(tmp_path, target=_PEEK)
        request = _zone_get("peek")
        with routed(tmp_path):
            match = resolve("/photo/3/")
            response = match.func(request, **match.kwargs)
            assert response.status_code == 200
            assert b"peek 3" in response.content
            benchmark(match.func, request, **match.kwargs)

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_intercepted(self, tmp_path: Path, benchmark) -> None:
        """The intercept page serves the zone, with no head to fold."""
        _photo_site(tmp_path, intercept=_PEEK)
        request = _zone_get("peek")
        with routed(tmp_path):
            match = resolve("/photo/3/")
            response = match.func(request, **match.kwargs)
            assert response.status_code == 200
            assert b"peek 3" in response.content
            assert b'"op": "meta"' not in response.content
            benchmark(match.func, request, **match.kwargs)

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_intercepted_with_meta(self, tmp_path: Path, benchmark) -> None:
        """The intercept declares metadata, so the envelope closes with a meta op."""
        _photo_site(tmp_path, intercept=_PEEK, meta=_PEEK_META)
        request = _zone_get("peek")
        with routed(tmp_path):
            match = resolve("/photo/3/")
            response = match.func(request, **match.kwargs)
            assert response.status_code == 200
            assert b"peek 3" in response.content
            assert json.loads(response.content)["ops"][-1]["title"] == "Peek"
            benchmark(match.func, request, **match.kwargs)
