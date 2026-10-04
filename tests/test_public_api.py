import importlib
import pkgutil
import subprocess
import sys

import pytest

import next as next_dj
from next import _LAZY_ATTRIBUTES
from next.components import component
from next.deps import Depends
from next.forms import action
from next.pages import context, page


_CURATED = frozenset({"Depends", "action", "component", "context", "page"})

AREA_EXPORTS: dict[str, frozenset[str]] = {
    "next.consent": frozenset(
        {
            "NECESSARY",
            "UNDECIDED",
            "Consent",
            "ConsentBackend",
            "CookieConsentBackend",
            "checks",
            "consent_categories",
            "get_consent",
            "signals",
        }
    ),
    "next.pages": frozenset(
        {
            "CacheControl",
            "CacheDict",
            "Context",
            "ContextResult",
            "HeadersDict",
            "HtmlMetadataRenderer",
            "MetadataDict",
            "MetadataRenderer",
            "Page",
            "PageContextShapeError",
            "PageMetadataConflictError",
            "PageMetadataRequestError",
            "PageMetadataShapeError",
            "PageMetadataTemplateError",
            "PageModuleImportError",
            "RESET",
            "Replace",
            "ResolvedMetadata",
            "checks",
            "context",
            "ld",
            "page",
            "signals",
        }
    ),
    "next.pages.metadata": frozenset(
        {
            "RESET",
            "Alternates",
            "AlternatesDict",
            "Article",
            "ArticleDict",
            "Book",
            "BookDict",
            "Breadcrumb",
            "Crumb",
            "Feed",
            "FeedDict",
            "GooglebotDict",
            "HtmlMetadataRenderer",
            "Icon",
            "IconDict",
            "IconsDict",
            "Link",
            "LinkDict",
            "Metadata",
            "MetadataDict",
            "MetadataRenderer",
            "OpenGraph",
            "OpenGraphAudio",
            "OpenGraphAudioDict",
            "OpenGraphDict",
            "OpenGraphImage",
            "OpenGraphImageDict",
            "OpenGraphVideo",
            "OpenGraphVideoDict",
            "OtherIconDict",
            "Profile",
            "ProfileDict",
            "Replace",
            "ResolvedMetadata",
            "Robots",
            "RobotsDict",
            "SiteMetadataDict",
            "SiteTitleDict",
            "Text",
            "ThemeColor",
            "ThemeColorDict",
            "TitleDict",
            "Twitter",
            "TwitterDict",
            "TwitterImage",
            "TwitterImageDict",
            "TwitterPlayer",
            "TwitterPlayerDict",
            "Verification",
            "VerificationDict",
            "Viewport",
            "ViewportDict",
            "absolute_url",
            "ld",
            "noindexed",
            "resolve_metadata",
        }
    ),
    "next.ports": frozenset(
        {
            "PageScan",
            "PageScripts",
            "PartialShaper",
            "PortSlot",
            "RouterAccess",
            "SeoRoutes",
            "StaticAssets",
            "page_scan_slot",
            "page_scripts_slot",
            "partial_shaper_slot",
            "router_access_slot",
            "seo_routes_slot",
            "static_assets_slot",
        }
    ),
    "next.scripts": frozenset(
        {"Script", "ScriptsSourceImportError", "Strategy", "checks", "signals"}
    ),
    "next.seo": frozenset(
        {
            "PageTreeSitemapBackend",
            "RobotsRule",
            "RobotsRuleError",
            "SeoSourceImportError",
            "SitemapBackend",
            "SitemapEntry",
            "SitemapEntryError",
            "SitemapTrailError",
            "checks",
            "signals",
            "sitemap",
        }
    ),
    "next.server": frozenset(
        {
            "NextStatReloader",
            "get_framework_filesystem_roots_for_linking",
            "iter_all_autoreload_watch_specs",
            "register_autoreload_watch_spec",
            "signals",
        }
    ),
    "next.site": frozenset(
        {"SiteOriginError", "site_indexable", "site_origin", "site_url"}
    ),
    "next.testing": frozenset(
        {
            "NextClient",
            "PartialEnvelope",
            "SignalEvent",
            "SignalRecorder",
            "SitemapUrl",
            "StaticCollectorProxy",
            "assert_has_class",
            "assert_metadata",
            "assert_missing_class",
            "build_form_for",
            "capture_framework_signals",
            "capture_signals",
            "clear_loaded_dirs",
            "eager_load_components",
            "eager_load_pages",
            "envelope_of",
            "find_anchor",
            "find_form",
            "form_action",
            "form_fields",
            "hidden_fields",
            "init_payload",
            "make_resolution_context",
            "override_component_backends",
            "override_dependency",
            "override_form_action",
            "override_next_settings",
            "override_provider",
            "parse_sitemap",
            "patch_static_collector",
            "render_component_by_name",
            "render_page",
            "reset_component_templates",
            "reset_components",
            "reset_failure_logs",
            "reset_form_actions",
            "reset_form_registration_state",
            "reset_page_cache",
            "reset_registries",
            "reset_scripts",
            "reset_seo",
            "resolve_action_url",
            "resolve_call",
        }
    ),
}


class TestCuratedSurface:
    """`next.__all__` is the curated top-level facade plus the version constant."""

    def test_all_matches_curated_set(self) -> None:
        assert frozenset(next_dj.__all__) == _CURATED | {"VERSION"}

    def test_all_has_no_duplicates(self) -> None:
        assert len(next_dj.__all__) == len(set(next_dj.__all__))

    def test_lazy_map_covers_every_curated_name(self) -> None:
        assert set(_LAZY_ATTRIBUTES) == _CURATED

    @pytest.mark.parametrize("name", sorted(_CURATED))
    def test_every_curated_name_resolves(self, name: str) -> None:
        assert getattr(next_dj, name) is not None

    def test_curated_names_are_the_subpackage_objects(self) -> None:
        assert next_dj.page is page
        assert next_dj.context is context
        assert next_dj.component is component
        assert next_dj.action is action
        assert next_dj.Depends is Depends

    def test_unknown_name_raises_attribute_error(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'nonexistent'"):
            next_dj.__getattr__("nonexistent")

    def test_hasattr_contract_stays_honest(self) -> None:
        assert hasattr(next_dj, "page")
        assert not hasattr(next_dj, "nonexistent")

    def test_lazy_names_do_not_shadow_subpackages(self) -> None:
        subpackages = {info.name for info in pkgutil.iter_modules(next_dj.__path__)}
        assert _CURATED & subpackages == frozenset()


class TestDirContract:
    """Module __dir__ lists the curated names on top of the live namespace."""

    def test_dir_covers_all(self) -> None:
        assert set(next_dj.__all__) <= set(next_dj.__dir__())

    def test_dir_is_sorted(self) -> None:
        listed = next_dj.__dir__()
        assert listed == sorted(listed)

    def test_dir_has_no_duplicates(self) -> None:
        listed = next_dj.__dir__()
        assert len(listed) == len(set(listed))

    def test_dir_lists_the_metadata_globals(self) -> None:
        assert {"__title__", "__version__", "__author__"} <= set(next_dj.__dir__())

    def test_dir_lists_an_imported_subpackage(self) -> None:
        importlib.import_module("next.deps")
        assert "deps" in next_dj.__dir__()


class TestImportStaysDjangoFree:
    """Importing next must not drag Django into a fresh interpreter."""

    def test_no_django_modules_after_import(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "import next\n"
                    "print(len([m for m in sys.modules if m.startswith('django')]))"
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "0"


class TestAreaSurfaces:
    """Each curated area package names exactly what it exports."""

    @pytest.mark.parametrize(
        ("module", "exported"), AREA_EXPORTS.items(), ids=list(AREA_EXPORTS)
    )
    def test_exported_names_are_pinned(
        self, module: str, exported: frozenset[str]
    ) -> None:
        """A dropped name coming back and a new one both have to be decided."""
        package = importlib.import_module(module)
        assert set(package.__all__) == exported
        assert all(hasattr(package, name) for name in exported)
