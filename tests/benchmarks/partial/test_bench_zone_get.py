from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from next.pages.loaders import _load_python_module_memo
from next.pages.manager import page as page_singleton
from tests.benchmarks.factories import build_layout_page
from tests.support import partial_meta, plain_get


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
    """Context provider priced near zero, so the tick overhead stays visible."""
    return "v"


@contextmanager
def _zone_view(
    directory: Path, *, providers: int = 0
) -> Iterator[Callable[..., HttpResponseBase]]:
    """Build the unified view of a ten-zone page with `providers` zone-gated callables.

    Each provider is bound to its own zone, so a tick for one zone runs at most
    one of them and the rest are the filter cost the batch pays. Registrations
    land in the module-level page singleton, so teardown takes them back out.
    """
    page_file = build_layout_page(directory, layouts=2, template=_ZONED_TEMPLATE)
    registry = page_singleton._context_manager
    for i in range(providers):
        registry.register_context(page_file, f"k_{i}", _cheap_provider, zone=f"z_{i}")
    module = _load_python_module_memo(page_file)
    view = page_singleton._create_unified_view(page_file, {}, module)
    try:
        yield view
    finally:
        registry._context_registry.pop(page_file, None)
        page_singleton.clear_template_caches()


@contextmanager
def _manifest_storage(root: Path) -> Iterator[None]:
    """Point staticfiles at a manifest storage carrying an unhashed manifest.

    An empty recorded hash is what sends `asset_version` down the hashing
    branch, which every partial response pays before it renders anything.
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
