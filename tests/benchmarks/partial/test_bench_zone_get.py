from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from django.test import RequestFactory, override_settings
from django.urls import resolve

from next.forms.uid import ORIGIN_FIELD_NAME
from next.pages.loaders import load_python_module_memo
from next.pages.manager import page as page_singleton
from next.partial import Patches, resolve_partial_origin
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


_CALLABLE_META = (
    "@page.metadata\n"
    "def head():\n"
    '    return {"title": "Peek", "description": "A peek", "canonical": True}\n'
)
_RULE_COUNT = 5
_RULE_ZONES = tuple(f"rule_{i}" for i in range(_RULE_COUNT))


def _ruled_site(root: Path) -> None:
    """Write a target with `_RULE_COUNT` rules over it, only the last one serving."""
    write_page(root, "photo/[int:pk]", PAGE_HEAD, body=_PHOTO)
    for i, zone in enumerate(_RULE_ZONES):
        trail = (
            "(.)photo/[int:pk]" if i == _RULE_COUNT - 1 else f"(.)photo/[int:pk]/r{i}"
        )
        body = f'{{% zone "{zone}" %}}<h2>{zone} {{{{ pk }}}}</h2>{{% endzone %}}'
        write_page(root, trail, PAGE_HEAD, body=body)


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
        """The intercept page serves the zone and its envelope closes on a null meta."""
        _photo_site(tmp_path, intercept=_PEEK)
        request = _zone_get("peek")
        with routed(tmp_path):
            match = resolve("/photo/3/")
            response = match.func(request, **match.kwargs)
            assert response.status_code == 200
            assert b"peek 3" in response.content
            assert b'"op":"meta"' in response.content
            benchmark(match.func, request, **match.kwargs)

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_intercepted_with_meta(self, tmp_path: Path, benchmark) -> None:
        """The intercept declares a metadata dict, so its meta op carries the head."""
        _photo_site(tmp_path, intercept=_PEEK, meta=_PEEK_META)
        request = _zone_get("peek")
        with routed(tmp_path):
            match = resolve("/photo/3/")
            response = match.func(request, **match.kwargs)
            assert response.status_code == 200
            assert b"peek 3" in response.content
            assert json.loads(response.content)["ops"][-1]["title"] == "Peek"
            benchmark(match.func, request, **match.kwargs)

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_intercepted_with_callable_meta(
        self, tmp_path: Path, benchmark
    ) -> None:
        """The intercept declares a callable `@page.metadata`, folded on every tick."""
        _photo_site(tmp_path, intercept=_PEEK, meta=_CALLABLE_META)
        request = _zone_get("peek")
        with routed(tmp_path):
            match = resolve("/photo/3/")
            response = match.func(request, **match.kwargs)
            assert response.status_code == 200
            assert b"peek 3" in response.content
            assert json.loads(response.content)["ops"][-1]["title"] == "Peek"
            benchmark(match.func, request, **match.kwargs)

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_zone_get_among_several_rules(self, tmp_path: Path, benchmark) -> None:
        """`pick_intercept` scans the rules of one target up to the last that serves."""
        _ruled_site(tmp_path)
        request = _zone_get(_RULE_ZONES[-1])
        with routed(tmp_path):
            match = resolve("/photo/3/")
            response = match.func(request, **match.kwargs)
            assert response.status_code == 200
            assert f"{_RULE_ZONES[-1]} 3".encode() in response.content
            benchmark(match.func, request, **match.kwargs)


class TestBenchLayerPost:
    """A POST from a layer, morphing the zone of the host page it opened over."""

    @pytest.mark.benchmark(group="partial.zone_get")
    def test_layer_post_morphs_host_zone(self, tmp_path: Path, benchmark) -> None:
        _photo_site(tmp_path, target=_PEEK, intercept=_PEEK)
        with routed(tmp_path):
            resolve("/photo/3/")

            def run() -> bytes:
                request = RequestFactory().post(
                    "/_next/form/x/",
                    data={ORIGIN_FIELD_NAME: "/photo/3/"},
                    **partial_meta("/photo/3/"),
                )
                origin = resolve_partial_origin(request)
                assert origin is not None
                envelope = (
                    Patches(request)
                    .morph(
                        zone="peek", page=origin.page_path, url_kwargs=origin.url_kwargs
                    )
                    .envelope()
                )
                return json.dumps(envelope.ops[0].as_dict()).encode()

            assert b"peek 3" in run()
            benchmark(run)
