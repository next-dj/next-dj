"""End-to-end tests for the static assets example.

The tests cover every Python module under ``myapp`` and ``config`` and exercise
every static asset feature wired up by the example. They verify co-located CSS
and JS discovery, module-level ``styles``/``scripts`` lists, the
``{% use_style %}``/``{% use_script %}`` template tags, the
``{% collect_styles %}``/``{% collect_scripts %}`` injection slots, the
``/_next/static/`` serving view, and the relative ordering guaranteed by
``StaticCollector`` and ``StaticManager``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from config import urls as config_urls
from myapp.apps import MyAppConfig
from myapp.pages import page as home_page
from myapp.pages._components.widget import component as widget_component
from myapp.pages.dashboard import page as dashboard_page
from myapp.pages.dashboard._components.chart import component as chart_component

from next.static import (
    SCRIPTS_PLACEHOLDER,
    STYLES_PLACEHOLDER,
    FileStaticBackend,
    StaticAsset,
    StaticCollector,
    StaticManager,
    static_manager,
)


if TYPE_CHECKING:
    from django.test import Client


BOOTSTRAP_CSS = (
    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
)
BOOTSTRAP_JS = (
    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"
)
BOOTSTRAP_ICONS = (
    "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"
)
CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"
INTER_FONT = "https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap"
JETBRAINS_MONO = (
    "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap"
)
REACT_CDN = "https://unpkg.com/react@18.3.1/umd/react.production.min.js"
REACT_DOM_CDN = "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js"
BABEL_CDN = "https://unpkg.com/@babel/standalone@7.24.7/babel.min.js"
COUNTER_CDNS = (REACT_CDN, REACT_DOM_CDN, BABEL_CDN)


def _read_streamed(response) -> bytes:
    """Collect body bytes from a streaming or regular ``HttpResponse``."""
    if getattr(response, "streaming", False):
        return b"".join(chunk for chunk in response.streaming_content)
    return response.content


def _assert_in_order(html: str, needles: list[str]) -> None:
    """Assert that every needle appears in ``html`` in the given order."""
    positions: list[int] = []
    for needle in needles:
        idx = html.find(needle)
        assert idx != -1, f"Expected to find {needle!r} in rendered HTML"
        positions.append(idx)
    assert positions == sorted(positions), (
        "Expected assets in order, got positions "
        f"{list(zip(needles, positions, strict=True))}"
    )


class TestHomePageRendering:
    """Verify the rendered home page (``/``) exposes the right assets."""

    def test_status_and_layout_shell(self, home_html: str) -> None:
        """The layout shell markers from ``layout.djx`` render on the home page."""
        assert "Static Assets Example" in home_html
        assert "next-dj static demo" in home_html

    def test_home_page_title_context_rendered(self, home_html: str) -> None:
        """The ``@context('page_title')`` value from ``page.py`` reaches the HTML."""
        assert ">Home</h1>" in home_html

    def test_widget_title_and_subtitle_rendered(self, home_html: str) -> None:
        """The widget component and its context keys render inside the page."""
        assert "Bootstrap widget" in home_html
        assert "Toggle details" in home_html

    def test_placeholders_are_replaced(self, home_html: str) -> None:
        """Collect slot placeholders do not leak into the final HTML."""
        assert STYLES_PLACEHOLDER not in home_html
        assert SCRIPTS_PLACEHOLDER not in home_html

    @pytest.mark.parametrize(
        "url",
        [
            BOOTSTRAP_CSS,
            BOOTSTRAP_ICONS,
            INTER_FONT,
            "/_next/static/layout.css",
            "/_next/static/index.css",
            "/_next/static/components/widget.css",
            "/_next/static/components/counter.css",
        ],
    )
    def test_contains_css_link(self, home_html: str, url: str) -> None:
        """Every expected CSS source is emitted as a ``<link>`` tag."""
        assert f'href="{url}"' in home_html

    @pytest.mark.parametrize(
        "url",
        [
            BOOTSTRAP_JS,
            "/_next/static/layout.js",
            "/_next/static/index.js",
            "/_next/static/components/widget.js",
            REACT_CDN,
            REACT_DOM_CDN,
            BABEL_CDN,
        ],
    )
    def test_contains_script_tag(self, home_html: str, url: str) -> None:
        """Every expected JS source is emitted as a ``<script>`` tag."""
        assert f'src="{url}"' in home_html

    def test_style_tags_render_in_head(self, home_html: str) -> None:
        """CSS link tags appear before the closing ``</head>`` marker."""
        head_end = home_html.index("</head>")
        for url in (BOOTSTRAP_CSS, INTER_FONT, "/_next/static/layout.css"):
            assert home_html.index(f'href="{url}"') < head_end

    def test_script_tags_render_before_body_close(self, home_html: str) -> None:
        """Script tags appear before the closing ``</body>`` marker."""
        body_end = home_html.index("</body>")
        for url in (BOOTSTRAP_JS, "/_next/static/components/widget.js"):
            assert home_html.index(f'src="{url}"') < body_end

    def test_styles_follow_nested_discovery_order(self, home_html: str) -> None:
        """Styles cascade from layout ``use_style`` deps down to component files."""
        _assert_in_order(
            home_html,
            [
                f'href="{BOOTSTRAP_CSS}"',
                'href="/_next/static/layout.css"',
                'href="/_next/static/index.css"',
                f'href="{INTER_FONT}"',
                'href="/_next/static/components/widget.css"',
                f'href="{BOOTSTRAP_ICONS}"',
                'href="/_next/static/components/counter.css"',
            ],
        )

    def test_scripts_follow_nested_discovery_order(self, home_html: str) -> None:
        """``use_script`` deps prepend (layout first, counter next) before co-located files."""
        _assert_in_order(
            home_html,
            [
                f'src="{BOOTSTRAP_JS}"',
                f'src="{REACT_CDN}"',
                f'src="{REACT_DOM_CDN}"',
                f'src="{BABEL_CDN}"',
                'src="/_next/static/layout.js"',
                'src="/_next/static/index.js"',
                'src="/_next/static/components/widget.js"',
            ],
        )


class TestDashboardPageRendering:
    """Verify the dashboard page (``/dashboard/``) exposes the right assets."""

    def test_status_and_page_shell(self, dashboard_html: str) -> None:
        """The layout shell and page heading render on the dashboard page."""
        assert "Dashboard" in dashboard_html
        assert "next-dj static demo" in dashboard_html

    def test_chart_labels_rendered(self, dashboard_html: str) -> None:
        """The chart's ``labels`` context reaches the canvas data attribute."""
        assert 'data-chart-labels="Jan,Feb,Mar,Apr"' in dashboard_html

    def test_chart_values_rendered(self, dashboard_html: str) -> None:
        """The chart's ``values`` context reaches the canvas data attribute."""
        assert 'data-chart-values="40,70,55,90"' in dashboard_html

    def test_placeholders_are_replaced(self, dashboard_html: str) -> None:
        """Collect slot placeholders do not leak into the final HTML."""
        assert STYLES_PLACEHOLDER not in dashboard_html
        assert SCRIPTS_PLACEHOLDER not in dashboard_html

    @pytest.mark.parametrize(
        "url",
        [
            BOOTSTRAP_CSS,
            JETBRAINS_MONO,
            "/_next/static/layout.css",
            "/_next/static/dashboard.css",
            "/_next/static/components/chart.css",
        ],
    )
    def test_contains_css_link(self, dashboard_html: str, url: str) -> None:
        """Every expected CSS source is emitted as a ``<link>`` tag."""
        assert f'href="{url}"' in dashboard_html

    @pytest.mark.parametrize(
        "url",
        [
            BOOTSTRAP_JS,
            CHART_JS,
            "/_next/static/layout.js",
            "/_next/static/components/chart.js",
        ],
    )
    def test_contains_script_tag(self, dashboard_html: str, url: str) -> None:
        """Every expected JS source is emitted as a ``<script>`` tag."""
        assert f'src="{url}"' in dashboard_html

    @pytest.mark.parametrize(
        "foreign",
        [
            INTER_FONT,
            BOOTSTRAP_ICONS,
            "/_next/static/index.css",
            "/_next/static/index.js",
            "/_next/static/components/widget.css",
            "/_next/static/components/widget.js",
            "/_next/static/components/counter.css",
            REACT_CDN,
            REACT_DOM_CDN,
            BABEL_CDN,
        ],
    )
    def test_dashboard_excludes_home_only_assets(
        self,
        dashboard_html: str,
        foreign: str,
    ) -> None:
        """Assets that only belong to the home page do not leak to the dashboard."""
        assert foreign not in dashboard_html

    def test_styles_follow_nested_discovery_order(self, dashboard_html: str) -> None:
        """Dashboard styles cascade from layout ``use_style`` deps down to components."""
        _assert_in_order(
            dashboard_html,
            [
                f'href="{BOOTSTRAP_CSS}"',
                'href="/_next/static/layout.css"',
                'href="/_next/static/dashboard.css"',
                f'href="{JETBRAINS_MONO}"',
                'href="/_next/static/components/chart.css"',
            ],
        )

    def test_scripts_follow_nested_discovery_order(self, dashboard_html: str) -> None:
        """Dashboard scripts cascade from layout ``use_script`` deps down to components."""
        _assert_in_order(
            dashboard_html,
            [
                f'src="{BOOTSTRAP_JS}"',
                'src="/_next/static/layout.js"',
                'src="/_next/static/components/chart.js"',
                f'src="{CHART_JS}"',
            ],
        )


class TestCounterDedup:
    """The counter component exercises dedup across repeated renders."""

    @pytest.mark.parametrize("mount_id", ["counter-likes", "counter-stars"])
    def test_counter_mount_points_rendered(self, home_html: str, mount_id: str) -> None:
        """Each counter instance renders its own mount ``<div>`` with a unique id."""
        assert f'id="{mount_id}"' in home_html

    @pytest.mark.parametrize(
        ("mount_id", "label"),
        [("counter-likes", "Likes"), ("counter-stars", "Stars")],
    )
    def test_counter_labels_rendered(
        self, home_html: str, mount_id: str, label: str
    ) -> None:
        """Literal ``label`` kwargs are passed through to the mount's dataset."""
        marker = f'id="{mount_id}" data-counter-label="{label}"'
        assert marker in home_html

    @pytest.mark.parametrize("url", COUNTER_CDNS)
    def test_each_react_cdn_appears_exactly_once(
        self, home_html: str, url: str
    ) -> None:
        """Counter is rendered twice, yet each CDN ``<script>`` shows up once."""
        assert home_html.count(f'src="{url}"') == 1

    def test_counter_component_css_registered_once(self, home_html: str) -> None:
        """Co-located ``counter/component.css`` is served under ``/_next/static/``."""
        needle = 'href="/_next/static/components/counter.css"'
        assert home_html.count(needle) == 1

    def test_inline_babel_blocks_split_into_shared_and_per_instance(
        self, home_html: str
    ) -> None:
        """Shared definition dedups to one, per-instance mount stays distinct.

        The counter splits its Babel payload into two blocks: one with the
        ``Counter`` component definition (identical for every instance, so
        content-based dedup collapses it to a single entry) and one with
        ``document.getElementById("counter-{{ id }}")`` mount code (which
        differs per instance and stays distinct). Rendering the counter
        twice produces three Babel blocks in the scripts slot: one shared
        definition plus one mount per instance.
        """
        assert home_html.count('<script type="text/babel"') == 3
        assert home_html.count("function Counter({ label })") == 1
        assert 'getElementById("counter-likes")' in home_html
        assert 'getElementById("counter-stars")' in home_html

    def test_inline_babel_blocks_hoisted_into_scripts_slot(
        self, home_html: str
    ) -> None:
        """``{% #use_script %}`` hoists the Babel blocks out of the mount region.

        The counter renders its mount ``<div>`` inline, but the Babel
        ``<script>`` bodies are captured by the block tag and placed inside
        the ``{% collect_scripts %}`` slot so every ``<script>`` sits
        together at the end of ``<body>``.
        """
        first_mount = home_html.find('id="counter-likes"')
        last_mount = home_html.rfind('id="counter-stars"')
        first_babel_script = home_html.find('<script type="text/babel"')
        assert first_mount != -1
        assert last_mount != -1
        assert first_babel_script != -1
        assert first_babel_script > last_mount

    def test_counter_use_script_deps_prepend_before_files(self, home_html: str) -> None:
        """``use_script`` deps declared in ``component.djx`` land before co-located files."""
        _assert_in_order(
            home_html,
            [
                f'src="{REACT_CDN}"',
                f'src="{REACT_DOM_CDN}"',
                f'src="{BABEL_CDN}"',
                'src="/_next/static/layout.js"',
                'src="/_next/static/components/widget.js"',
            ],
        )

    def test_inline_babel_blocks_follow_deps(self, home_html: str) -> None:
        """Inline ``{% #use_script %}`` bodies append after co-located/CDN scripts.

        React/ReactDOM/Babel CDNs prepend (URL-form ``use_script``), co-located
        ``*.js`` files append, and the Babel ``<script>`` bodies (one shared
        definition plus one mount per instance) append last -- exactly the
        order ``<script type="text/babel">`` needs to run after Babel
        Standalone has loaded and parsed the DOM.
        """
        first_babel = home_html.find('<script type="text/babel"')
        widget_js = home_html.find('src="/_next/static/components/widget.js"')
        babel_cdn = home_html.find(f'src="{BABEL_CDN}"')
        assert -1 not in (first_babel, widget_js, babel_cdn)
        assert first_babel > widget_js > babel_cdn


class TestStaticFileServing:
    """Verify ``/_next/static/`` serves the registered co-located assets."""

    @pytest.fixture(autouse=True)
    def _warm_registry(self, home_html: str, dashboard_html: str) -> None:
        """Render both pages so the backend registry contains every asset."""

    @pytest.mark.parametrize(
        ("path", "marker"),
        [
            ("/_next/static/layout.css", b"background-color"),
            ("/_next/static/layout.js", b"layout script loaded"),
            ("/_next/static/index.css", b"home-hero"),
            ("/_next/static/index.js", b"home template script loaded"),
            ("/_next/static/dashboard.css", b"JetBrains Mono"),
            ("/_next/static/components/widget.css", b".widget"),
            ("/_next/static/components/widget.js", b"widget-details"),
            ("/_next/static/components/chart.css", b".chart"),
            ("/_next/static/components/chart.js", b"initChart"),
        ],
    )
    def test_served_files_return_expected_content(
        self,
        client: Client,
        path: str,
        marker: bytes,
    ) -> None:
        """Each registered logical path serves the correct on-disk contents."""
        response = client.get(path)
        assert response.status_code == 200
        assert marker in _read_streamed(response)

    def test_unknown_path_returns_404(self, client: Client) -> None:
        """An unregistered path inside ``/_next/static/`` returns a 404 response."""
        response = client.get("/_next/static/does-not-exist.css")
        assert response.status_code == 404

    def test_conditional_get_returns_304(self, client: Client) -> None:
        """A conditional GET with a matching ``If-Modified-Since`` yields 304."""
        response = client.get("/_next/static/layout.css")
        assert response.status_code == 200
        last_modified = response["Last-Modified"]
        second = client.get(
            "/_next/static/layout.css",
            HTTP_IF_MODIFIED_SINCE=last_modified,
        )
        assert second.status_code == 304

    def test_served_view_rejects_non_file_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the default backend is not a ``FileStaticBackend``, the view 404s."""
        from django.test import RequestFactory  # noqa: PLC0415

        from next.static import static_serve_view  # noqa: PLC0415

        class _DummyBackend:
            pass

        monkeypatch.setattr(
            StaticManager,
            "default_backend",
            property(lambda self: _DummyBackend()),
        )
        response = static_serve_view(
            RequestFactory().get("/_next/static/layout.css"),
            "layout.css",
        )
        assert response.status_code == 404


class TestExampleModulesCoverage:
    """Directly exercise every symbol declared by example modules."""

    def test_app_config_attributes(self) -> None:
        """The ``MyAppConfig`` class declares the expected Django metadata."""
        assert MyAppConfig.name == "myapp"
        assert MyAppConfig.default_auto_field == "django.db.models.BigAutoField"

    def test_config_urls_include_next_patterns(self) -> None:
        """The root URL conf routes through ``next.urls`` only."""
        assert isinstance(config_urls.urlpatterns, list)
        assert len(config_urls.urlpatterns) == 1

    def test_home_page_module_vars(self) -> None:
        """Home ``page.py`` exposes the Inter font in ``styles`` and no scripts."""
        assert home_page.styles == [INTER_FONT]
        assert home_page.scripts == []

    def test_home_page_title_function_is_callable(self) -> None:
        """The ``get_page_title`` context function returns the home heading."""
        assert callable(home_page.get_page_title)
        assert home_page.get_page_title() == "Home"

    def test_dashboard_page_module_vars(self) -> None:
        """Dashboard ``page.py`` exposes JetBrains Mono in ``styles``."""
        assert dashboard_page.styles == [JETBRAINS_MONO]
        assert dashboard_page.scripts == []

    def test_widget_component_module_vars(self) -> None:
        """Widget ``component.py`` requests Bootstrap Icons via ``styles``."""
        assert widget_component.styles == [BOOTSTRAP_ICONS]
        assert widget_component.scripts == []

    def test_widget_subtitle_function_is_callable(self) -> None:
        """The widget's ``subtitle`` context function returns a static string."""
        assert callable(widget_component.widget_subtitle)
        assert widget_component.widget_subtitle() == "Composite widget example"

    def test_chart_component_module_vars(self) -> None:
        """Chart ``component.py`` requests Chart.js via ``scripts``."""
        assert chart_component.styles == []
        assert chart_component.scripts == [CHART_JS]

    def test_chart_label_and_value_functions(self) -> None:
        """The chart's label and value context functions return fixed fixtures."""
        assert callable(chart_component.chart_labels)
        assert callable(chart_component.chart_values)
        assert chart_component.chart_labels() == ["Jan", "Feb", "Mar", "Apr"]
        assert chart_component.chart_values() == [40, 70, 55, 90]


class TestStaticManagerWiring:
    """Verify the example's ``NEXT_FRAMEWORK`` settings wire up correctly."""

    def test_default_backend_is_file_static_backend(self) -> None:
        """The example settings expose a ``FileStaticBackend`` by default."""
        assert isinstance(static_manager, StaticManager)
        assert isinstance(static_manager.default_backend, FileStaticBackend)

    def test_collector_deduplicates_repeat_urls(self) -> None:
        """Adding the same URL twice to a collector keeps a single entry."""
        collector = StaticCollector()
        collector.add(StaticAsset(url=BOOTSTRAP_CSS, kind="css"))
        collector.add(StaticAsset(url=BOOTSTRAP_CSS, kind="css"))
        collector.add(StaticAsset(url=BOOTSTRAP_JS, kind="js"))
        collector.add(StaticAsset(url=BOOTSTRAP_JS, kind="js"))
        assert [asset.url for asset in collector.styles()] == [BOOTSTRAP_CSS]
        assert [asset.url for asset in collector.scripts()] == [BOOTSTRAP_JS]
