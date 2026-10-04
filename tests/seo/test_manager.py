import functools
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from unittest.mock import patch

import pytest
from django.contrib.sitemaps import Sitemap
from django.test import override_settings
from django.utils.functional import lazy

from next.conf.signals import settings_reloaded
from next.pages.responses import cache_control
from next.seo import (
    PageTreeSitemapBackend,
    SitemapBackend,
    manager as seo_manager_module,
)
from next.seo.discovery import BrokenSource
from next.seo.manager import (
    SeoManager,
    reset_seo_sources,
    seo_manager,
    sitemap_backend_entries,
    stable_repr,
)
from next.seo.registry import sitemap_items_registry
from next.seo.robots import DeclaredRobots, TextFile
from next.urls import router_manager
from next.urls.manager import seo_routes_version
from tests.django_setup import PROJECT_ROOT
from tests.support import CLOSED_SITE, POSTS_ITEMS, WITH_BASE, routed, write_tree


DEFAULT_ENTRY = {"BACKEND": "next.seo.PageTreeSitemapBackend", "OPTIONS": {}}


class ExtraBackend(SitemapBackend):
    """A backend serving one section named `extra`, cached for a minute."""

    def sections(self, request):
        """Answer the one section."""
        return {"extra": Sitemap(), "pages": Sitemap()}

    def cache_control(self):
        """Ask for a minute."""
        return cache_control(60)


class IdleBackend(SitemapBackend):
    """A backend with nothing to serve."""

    def sections(self, request):
        """Answer nothing."""
        return {}

    def serves(self):
        """Serve nothing."""
        return False


EXTRA = {"BACKEND": "tests.seo.test_manager.ExtraBackend"}
IDLE = {"BACKEND": "tests.seo.test_manager.IdleBackend"}


class TestSettings:
    """The `SEO` scope lists the backends, the page trees by default."""

    def test_the_default_is_the_page_tree_backend(self) -> None:
        assert sitemap_backend_entries() == [DEFAULT_ENTRY]
        [backend] = SeoManager().backends
        assert isinstance(backend, PageTreeSitemapBackend)

    def test_a_scope_without_the_key_keeps_the_default(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"SEO": {}}):
            assert sitemap_backend_entries() == [DEFAULT_ENTRY]

    def test_a_list_of_the_wrong_shape_falls_back_or_filters(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"SEO": {"SITEMAP_BACKENDS": "x"}}):
            assert sitemap_backend_entries() == [DEFAULT_ENTRY]
        with override_settings(
            NEXT_FRAMEWORK={"SEO": {"SITEMAP_BACKENDS": ["x", EXTRA]}}
        ):
            assert sitemap_backend_entries() == [EXTRA]

    def test_an_empty_list_serves_no_sitemap(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root, SEO={"SITEMAP_BACKENDS": []}):
            assert seo_manager.backends == ()
            assert seo_manager.serves_sitemap() is False

    def test_an_unresolvable_entry_is_skipped(self, caplog) -> None:
        with (
            override_settings(
                NEXT_FRAMEWORK={
                    "SEO": {"SITEMAP_BACKENDS": [{"BACKEND": "nope.Nope"}, EXTRA]}
                }
            ),
            caplog.at_level(logging.ERROR, logger="next.backends"),
        ):
            assert [type(b) for b in seo_manager.backends] == [ExtraBackend]
        assert "error resolving SitemapBackend" in caplog.text


class TestSections:
    """The sections of every backend merge, the first one keeping a shared name."""

    def test_the_first_backend_wins_a_shared_name(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        with routed(root, SEO={"SITEMAP_BACKENDS": [DEFAULT_ENTRY, EXTRA]}):
            sections = seo_manager.sections(None)
        assert list(sections) == ["pages", "extra"]
        assert isinstance(sections["pages"], Sitemap)
        assert type(sections["pages"]).__name__ == "PageTreeSitemap"

    def test_a_closed_site_lists_no_section(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root, **CLOSED_SITE):
            assert seo_manager.sections(None) == {}

    def test_serving_asks_every_backend(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root, SEO={"SITEMAP_BACKENDS": [IDLE]}):
            assert seo_manager.serves_sitemap() is False
        with routed(root, SEO={"SITEMAP_BACKENDS": [IDLE, EXTRA]}):
            assert seo_manager.serves_sitemap() is True

    def test_the_shortest_cache_of_the_backends_wins(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = 600\n")
        with routed(root, SEO={"SITEMAP_BACKENDS": [DEFAULT_ENTRY, EXTRA]}):
            assert seo_manager.cache_control() == cache_control(60)
        with routed(root, SEO={"SITEMAP_BACKENDS": [IDLE]}):
            assert seo_manager.cache_control() is None


class TestSources:
    """The robots source is chosen once per version."""

    def test_each_source_is_memoised_until_a_reset(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="")
        with routed(root):
            robots = seo_manager.robots_source()
            assert isinstance(robots, DeclaredRobots)
            assert seo_manager.robots_source() is robots
            seo_manager.reset()
            assert seo_manager.robots_source() is not robots

    def test_no_source_answers_none_once(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages")):
            assert seo_manager.robots_source() is None
            assert seo_manager.robots_source() is None

    @override_settings(DEBUG=True)
    def test_a_broken_robots_py_holds_the_route_over_its_robots_txt(
        self, tmp_path, caplog
    ) -> None:
        root = write_tree(
            tmp_path / "pages",
            robots="raise RuntimeError('boom')\n",
            robots_txt=b"User-agent: *\n",
        )
        with routed(root), caplog.at_level(logging.WARNING, logger="next.seo"):
            source = seo_manager.robots_source()
            seo_manager.reset()
            seo_manager.robots_source()
        assert source == BrokenSource(root / "robots.py")
        assert caplog.text.count(f"{root / 'robots.py'} serves it") == 1
        assert f"ignored: {root / 'robots.txt'}" in caplog.text

    def test_production_leaves_the_choice_to_the_checks(self, tmp_path, caplog) -> None:
        root = write_tree(tmp_path / "pages", robots="", robots_txt=b"x\n")
        with routed(root), caplog.at_level(logging.WARNING, logger="next.seo"):
            source = seo_manager.robots_source()
        assert isinstance(source, DeclaredRobots)
        assert "serves it" not in caplog.text

    def test_a_single_source_warns_nothing(self, tmp_path, caplog) -> None:
        with (
            routed(write_tree(tmp_path / "pages", robots="")),
            caplog.at_level(logging.WARNING, logger="next.seo"),
        ):
            seo_manager.robots_source()
        assert "sources" not in caplog.text


class TestResetDuringALookup:
    """A value computed while a reset happens is returned but never memoised."""

    def test_a_robots_source_found_across_a_reset_is_not_kept(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="")
        real = seo_manager_module.robots_candidates

        def candidates(roots):
            seo_manager.reset()
            return real(roots)

        with routed(root):
            with patch.object(seo_manager_module, "robots_candidates", candidates):
                found = seo_manager.robots_source()
            assert isinstance(found, DeclaredRobots)
            assert seo_manager.robots_source() is not found

    def test_a_fingerprint_taken_across_a_reset_is_not_kept(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = 60\n")
        real = seo_manager_module._source_bytes

        def source_bytes(roots):
            seo_manager.reset()
            return real(roots)

        with routed(root):
            with patch.object(seo_manager_module, "_source_bytes", source_bytes):
                seo_manager.fingerprint()
            assert seo_manager._fingerprint is None


class TestFingerprint:
    """The fingerprint follows the source bytes and the settings, once per version."""

    def test_it_is_memoised_and_moves_with_a_source(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = 60\n")
        with routed(root, **WITH_BASE):
            first = seo_manager.fingerprint()
            assert len(first) == 12
            assert seo_manager.fingerprint() is first
            (root / "sitemap.py").write_text("cache = 120\n")
            seo_manager.reset()
            assert seo_manager.fingerprint() != first

    def test_it_moves_with_the_site_settings(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root):
            plain = seo_manager.fingerprint()
        with routed(root, **WITH_BASE):
            assert seo_manager.fingerprint() != plain

    def test_equal_settings_in_another_order_agree(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        options = {"a": 1, "b": frozenset({"x", "y", "z"})}
        entry = {"BACKEND": "next.seo.PageTreeSitemapBackend", "OPTIONS": options}
        flipped = {
            "OPTIONS": dict(reversed(options.items())),
            "BACKEND": entry["BACKEND"],
        }
        prints = []
        for backend in (entry, flipped):
            with routed(root, SEO={"SITEMAP_BACKENDS": [backend]}):
                prints.append(seo_manager.fingerprint())
        assert prints[0] == prints[1]


def _site_url(request):
    return "https://acme.example"


def _hosts(request):
    return True


@dataclass(frozen=True)
class _Pair:
    left: object
    right: object


class _Rule:
    def __call__(self, request) -> bool:
        return True


_TEXT = lazy(lambda: "Acme", str)()


class TestStableRepr:
    """The representation ignores set order, addresses and laziness."""

    @pytest.mark.parametrize(
        ("value", "spelled"),
        [
            ({"b": 1, "a": 2}, "{'a':2,'b':1}"),
            (frozenset({"b", "a"}), "{'a','b'}"),
            (["b", "a"], "['b','a']"),
            (_site_url, f"{__name__}._site_url"),
            (_Rule(), f"{__name__}._Rule"),
            (functools.partial(_hosts), "functools.partial"),
            (_TEXT, "'Acme'"),
            (_Pair(1, None), "_Pair{'left':1,'right':None}"),
            (_Pair, f"{__name__}._Pair"),
        ],
    )
    def test_each_form_is_spelled_without_order_or_address(
        self, value, spelled
    ) -> None:
        assert stable_repr(value) == spelled

    def test_two_hash_seeds_spell_a_site_alike(self) -> None:
        spellings = {
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from tests.django_setup import setup\n"
                        "setup()\n"
                        "from next.seo.manager import stable_repr\n"
                        "from next.site.config import SiteConfig\n"
                        "hosts = frozenset(f'host-{n}.example' for n in range(32))\n"
                        "site = SiteConfig(url=setup, indexable=print)\n"
                        "print(stable_repr([site, hosts]))\n"
                    ),
                ],
                capture_output=True,
                check=True,
                cwd=PROJECT_ROOT,
                env={**os.environ, "PYTHONHASHSEED": seed},
                text=True,
            ).stdout
            for seed in ("1", "2")
        }
        assert len(spellings) == 1
        assert "tests.django_setup.setup" in spellings.pop()


class TestRefresh:
    """Under `DEBUG` an SEO source is read again once its file changes on disk."""

    def test_a_watched_edit_resets_the_sources(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root), override_settings(DEBUG=True):
            assert seo_manager.robots_source() is None
            (root / "robots.txt").write_bytes(b"User-agent: *\n")
            version = seo_manager.version
            seo_manager.refresh()
            assert seo_manager.version != version
            assert isinstance(seo_manager.robots_source(), TextFile)

    def test_an_unmoved_tree_keeps_the_sources(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots_txt=b"User-agent: *\n")
        with routed(root), override_settings(DEBUG=True):
            roots = seo_manager.roots()
            seo_manager.refresh()
            assert seo_manager.roots() is roots

    def test_an_unwatched_edit_waits_for_a_reset(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages")
        with routed(root):
            roots = seo_manager.roots()
            (root / "robots.txt").write_bytes(b"User-agent: *\n")
            seo_manager.refresh()
            assert seo_manager.roots() is roots


class TestReset:
    """A reset drops the backends and the sources and moves the version."""

    def test_a_reset_reloads_the_backends(self) -> None:
        [before] = seo_manager.backends
        version = seo_manager.version
        seo_manager.reset()
        assert seo_manager.version != version
        assert seo_manager.backends[0] is not before

    def test_the_settings_and_the_router_reload_reset_it(self) -> None:
        version = seo_manager.version
        settings_reloaded.send(sender=None)
        assert seo_manager.version != version
        version = seo_manager.version
        router_manager.reload()
        assert seo_manager.version != version

    def test_every_manager_reads_the_routes_token(self) -> None:
        assert SeoManager().version == seo_routes_version.value

    def test_reset_seo_sources_drops_every_registration(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            seo_manager.roots()
            assert sitemap_items_registry.entries_for(root / "sitemap.py")
            reset_seo_sources()
            assert sitemap_items_registry.entries_for(root / "sitemap.py") == ()
