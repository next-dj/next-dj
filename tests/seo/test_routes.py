from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import URLPattern

from next.diagnostics import degraded, watch_degraded
from next.seo import routes
from next.seo.manager import seo_manager
from next.seo.routes import SeoPatterns, served_names, served_patterns
from next.seo.urls import PATTERNS
from tests.support import routed, write_tree


ALL = ["sitemap", "sitemap_section", "robots"]


class TestRouteConstants:
    """The paths and names of the routes are defined in one module."""

    def test_the_addresses_and_the_names(self) -> None:
        assert [str(pattern.pattern) for pattern in PATTERNS] == [
            routes.SITEMAP_ROUTE,
            routes.SECTION_ROUTE,
            routes.ROBOTS_ROUTE,
        ]
        assert [pattern.name for pattern in PATTERNS] == ALL
        assert (routes.DEFAULT_NAMESPACE, routes.HOST_ROOT_NAMESPACE) == (
            "next",
            "next_seo",
        )


class TestServed:
    """A route is served only while its source exists, broken or not."""

    @pytest.mark.parametrize(
        ("sources", "names"),
        [
            ({}, set()),
            ({"sitemap": ""}, {"sitemap", "sitemap_section"}),
            ({"sitemap": "raise ValueError\n"}, {"sitemap", "sitemap_section"}),
            ({"robots": ""}, {"robots"}),
            ({"robots": "raise ValueError\n"}, {"robots"}),
            ({"robots_txt": b"User-agent: *\n"}, {"robots"}),
        ],
        ids=[
            "none",
            "sitemap",
            "broken-sitemap",
            "robots-py",
            "broken-robots-py",
            "robots-txt",
        ],
    )
    def test_a_route_needs_its_source(self, tmp_path, sources, names) -> None:
        with routed(write_tree(tmp_path / "pages", **sources)):
            assert served_names() == names
            assert {pattern.name for pattern in served_patterns(PATTERNS)} == names


class TestFailingProbe:
    """A probe that raises keeps its route, contained like any project failure."""

    def test_a_raising_probe_keeps_the_route_and_degrades(self, tmp_path) -> None:
        with (
            routed(write_tree(tmp_path / "pages")),
            patch.object(seo_manager, "robots_source", side_effect=OSError("disk")),
        ):
            watch_degraded()
            assert served_names() == {"robots"}
            assert degraded()
        watch_degraded()

    @override_settings(DEBUG=True)
    def test_debug_raises_naming_the_route(self, tmp_path) -> None:
        with (
            routed(write_tree(tmp_path / "pages")),
            patch.object(seo_manager, "robots_source", side_effect=OSError("disk")),
            pytest.raises(OSError, match="disk") as caught,
        ):
            served_names()
        assert caught.value.__notes__ == [
            (
                "Deciding whether to mount /robots.txt raised, so the route stays "
                "mounted and answers 503 until its source loads."
            )
        ]


class TestSeoPatterns:
    """The lazy patterns filter once per manager version."""

    def test_a_sequence_refiltered_after_a_reset(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="")
        patterns = SeoPatterns(PATTERNS)
        with routed(root):
            assert [pattern.name for pattern in patterns] == ["robots"]
            assert len(patterns) == 1
            assert isinstance(patterns[0], URLPattern)
            assert patterns[:1] == (patterns[0],)
            (root / "sitemap.py").write_text("")
            assert len(patterns) == 1
            seo_manager.reset()
            assert [pattern.name for pattern in patterns] == ALL
