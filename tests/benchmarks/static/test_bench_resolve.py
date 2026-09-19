from __future__ import annotations

import pytest
from django.template import Context, Template, engines

from next.static.assets import with_version
from next.static.backends import StaticFilesBackend


engines.all()


_COLD_ROUNDS = 50
_COLD_WARMUP_ROUNDS = 5

_NAME = "css/theme.css"
_READY_URL = "https://cdn.example.com/a.css"
_PLAIN_URL = "/static/css/app.css"
_QUERIED_URL = "/static/css/app.css?build=7"
_VERSIONED_URL = "/static/css/app.css?v=8e1a0d"
_DATA_URI = "data:text/css;base64,Ym9keXt9"
_VERSION = "9f2c1b"

_ASSET_SOURCE = '{% load next_static %}{% asset "css/app.css" %}'
_STATIC_SOURCE = '{% load static %}{% static "css/app.css" %}'


def _warm_backend() -> StaticFilesBackend:
    """Return a backend whose memo already holds both references."""
    backend = StaticFilesBackend()
    backend.resolve_url(_NAME)
    backend.resolve_url(_READY_URL)
    return backend


def _warm_template(source: str) -> tuple[Template, Context]:
    """Compile the source and render it once, so the timed round is steady state."""
    template = Template(source)
    context = Context({})
    template.render(context)
    return template, context


class TestBenchResolveUrl:
    """What `resolve_url` costs per tag, warm, cold, and on a reference it leaves alone.

    Every `{% use_style %}`, `{% use_script %}`, and module-list entry pays one call.
    """

    @pytest.mark.benchmark(group="static.names")
    def test_resolve_name_warm(self, benchmark) -> None:
        """A primed memo answers, the steady state every render after the first meets."""
        backend = _warm_backend()
        benchmark(backend.resolve_url, _NAME)

    @pytest.mark.benchmark(group="static.names")
    def test_resolve_name_cold(self, benchmark) -> None:
        """The memo is dropped per round, so storage answers rather than the cache."""
        backend = _warm_backend()

        def setup() -> tuple[tuple[str], dict[str, object]]:
            backend.forget_urls()
            return (_NAME,), {}

        benchmark.pedantic(
            backend.resolve_url,
            setup=setup,
            rounds=_COLD_ROUNDS,
            warmup_rounds=_COLD_WARMUP_ROUNDS,
        )

    @pytest.mark.benchmark(group="static.names")
    def test_resolve_ready_url_passthrough(self, benchmark) -> None:
        """A ready URL, what every template in the wild holds, is pure added cost."""
        backend = _warm_backend()
        benchmark(backend.resolve_url, _READY_URL)


class TestBenchAssetTag:
    """`{% asset %}` beside `{% static %}`, the Django tag it stands next to."""

    @pytest.mark.benchmark(group="static.names")
    def test_asset_tag(self, benchmark) -> None:
        template, context = _warm_template(_ASSET_SOURCE)
        benchmark(template.render, context)

    @pytest.mark.benchmark(group="static.names")
    def test_django_static_tag(self, benchmark) -> None:
        template, context = _warm_template(_STATIC_SOURCE)
        benchmark(template.render, context)


class TestBenchVersionAppend:
    """`with_version` rebuilds a URL through `urlsplit`, the priciest step per URL."""

    @pytest.mark.benchmark(group="static.version")
    def test_version_on_bare_url(self, benchmark) -> None:
        benchmark(with_version, _PLAIN_URL, _VERSION)

    @pytest.mark.benchmark(group="static.version")
    def test_version_on_queried_url(self, benchmark) -> None:
        """A URL already carrying a query keeps its pairs and gains one more."""
        benchmark(with_version, _QUERIED_URL, _VERSION)

    @pytest.mark.benchmark(group="static.version")
    def test_version_on_versioned_url(self, benchmark) -> None:
        """The replace path, what a second stamp over a stamped URL walks."""
        benchmark(with_version, _VERSIONED_URL, _VERSION)

    @pytest.mark.benchmark(group="static.version")
    def test_version_on_opaque_uri(self, benchmark) -> None:
        """An opaque URI short-circuits after the split, so it is the floor."""
        benchmark(with_version, _DATA_URI, _VERSION)
