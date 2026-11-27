import ast
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
from next.testing import NextClient
from tests.support.imports import package_root


_CURATED = frozenset({"Depends", "action", "component", "context", "page"})

AREA_EXPORTS: dict[str, frozenset[str]] = {
    "next.apps": frozenset({"NextFrameworkConfig", "checks"}),
    "next.boundaries": frozenset(
        {"BoundaryOptionError", "ErrorKind", "boundary_failed", "contain"}
    ),
    "next.bridge": frozenset({"drive"}),
    "next.checks": frozenset({"NEXT", "SEO", "register"}),
    "next.components": frozenset(
        {
            "CachedComponentTemplateLoader",
            "ComponentContextManager",
            "ComponentContextRegistry",
            "ComponentContextConflictError",
            "ComponentInfo",
            "ComponentModuleImportError",
            "ComponentRegistry",
            "ComponentRenderStrategy",
            "ComponentRenderer",
            "ComponentScanner",
            "ComponentTemplateLoader",
            "ComponentVisibilityResolver",
            "ComponentsBackend",
            "ComponentsManager",
            "CompositeComponentRenderer",
            "ContextFunction",
            "FileComponentsBackend",
            "ModuleCache",
            "ModuleLoader",
            "SimpleComponentRenderer",
            "checks",
            "collect_visible_components",
            "component",
            "component_extra_roots_from_config",
            "component_watch_roots",
            "components_manager",
            "context",
            "get_component",
            "get_component_paths_for_watch",
            "load_component_template",
            "refresh_component_files",
            "register_components_folder_from_router_walk",
            "render_component",
            "signals",
        }
    ),
    "next.compression": frozenset(
        {
            "BrotliBackend",
            "CodecUnavailableError",
            "CompressionBackend",
            "CompressionBackendError",
            "Compressor",
            "GzipBackend",
            "ZstdBackend",
            "compression_backend_loaded",
            "decode_response",
            "no_compress",
        }
    ),
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
    "next.deps": frozenset(
        {
            "AnsweringParameterProvider",
            "AsyncCompilingParameterProvider",
            "AsyncDependencyError",
            "AsyncParameterFiller",
            "ContextReads",
            "DDependencyBase",
            "DObject",
            "DependencyCycleError",
            "DependencyResolver",
            "Depends",
            "Loader",
            "MissingKeyError",
            "ParameterFiller",
            "ParameterProvider",
            "ProviderRegistry",
            "READS_EVERY",
            "READS_NOTHING",
            "REQUEST_DEP_CACHE_ATTR",
            "RESERVED_KEYS",
            "ReachingParameterProvider",
            "ReadingParameterProvider",
            "RegisteredParameterProvider",
            "ResolutionContext",
            "UnboundObjectError",
            "UnknownDependencyError",
            "provider_registry",
            "render_dep_cache",
            "resolver",
            "rows",
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
            "TemplateLoader",
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
            "ActionOwners",
            "DeferralSink",
            "FormRoutes",
            "LayerLoading",
            "PageScan",
            "PageScripts",
            "PageViews",
            "PortList",
            "PortSlot",
            "Ranked",
            "RouteAccess",
            "RouterStamp",
            "ScriptRender",
            "ScriptShare",
            "SeoRoutes",
            "ServerLoad",
            "StaticAssets",
            "ZoneMarking",
            "action_owners_slot",
            "deferrals_slot",
            "form_routes_slot",
            "layer_loading_slot",
            "marked_zones",
            "page_scan_slot",
            "page_scripts",
            "page_views_slot",
            "render_deferred",
            "route_access_slot",
            "router_stamp_slot",
            "seo_routes_slot",
            "server_backlog",
            "server_load_slot",
            "stamp_router",
            "stash_layer_loading",
            "static_assets_slot",
            "zone_marking_slot",
        }
    ),
    "next.permissions": frozenset(
        {
            "DeclarationOutsideModuleError",
            "ObjectPermissionBackend",
            "PermissionAlreadyRegisteredError",
            "UnknownAccessObjectError",
            "add_perm",
            "has_perm",
        }
    ),
    "next.realtime": frozenset(
        {
            "BrokerBackend",
            "BrokerConnectionError",
            "InvalidMemberError",
            "InvalidTicketError",
            "InvalidTopicError",
            "MemoryBroker",
            "Message",
            "Position",
            "Presence",
            "PresenceBroker",
            "RedisBroker",
            "Replay",
            "ReplayingBroker",
            "SessionBinding",
            "SignedCookieBinding",
            "TopicPayloadError",
            "TopicSocket",
            "TopicStream",
            "Upstream",
            "WebSocketApp",
            "application",
            "apresence",
            "apublish",
            "on",
            "presence",
            "publish",
        }
    ),
    "next.router": frozenset(
        {
            "ChromeDigest",
            "Cut",
            "CutKind",
            "ClientLayouts",
            "Divergence",
            "LayoutKey",
            "LoadShed",
            "NavigationCut",
            "PrefetchShed",
            "ScannedLayout",
            "ScannedPage",
            "VerifiedCut",
            "navigation_rendered",
            "prefetch_shed",
            "router_backend_loaded",
            "router_clear",
            "router_cleared",
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
    "next.static": frozenset(
        {
            "AssetDiscovery",
            "DedupStrategy",
            "DeepMergePolicy",
            "FirstWinsPolicy",
            "HashContentDedup",
            "IdentityDedup",
            "JsContextPolicy",
            "JsContextSerializer",
            "JsonJsContextSerializer",
            "KindRegistry",
            "LastWinsPolicy",
            "NextAppDirectoriesFinder",
            "NextScriptBuilder",
            "NextStaticFilesFinder",
            "PlaceholderRegistry",
            "PlaceholderSlot",
            "PydanticJsContextSerializer",
            "RaiseOnConflictPolicy",
            "ScriptInjectionPolicy",
            "StaticAsset",
            "StaticAssetNotFoundError",
            "StaticAssetTraversalError",
            "StaticBackend",
            "StaticCollector",
            "StaticFilesBackend",
            "StaticManager",
            "UrlDedup",
            "checks",
            "collect_component_assets",
            "default_kinds",
            "default_manager",
            "default_placeholders",
            "default_stems",
            "get_static_manager",
            "register_defaults",
            "reset_default_manager",
            "resolve_serializer",
            "signals",
            "static_name",
        }
    ),
    "next.defer": frozenset(
        {
            "Admission",
            "Admit",
            "BoundaryBindingError",
            "BoundaryClaims",
            "BoundaryError",
            "BoundaryExecutor",
            "BoundaryMintError",
            "BoundaryPropsError",
            "BoundarySizeError",
            "BoundaryStorageError",
            "BoundaryTarget",
            "BoundaryTokenBackend",
            "BoundaryTokenError",
            "BoundaryValueError",
            "CacheStorage",
            "Capacity",
            "ConnectionState",
            "DefaultDeliveryPolicy",
            "DefaultServerProbe",
            "DeliveryDecision",
            "Delivery",
            "DeliveryReason",
            "EncryptedSeal",
            "InlineStorage",
            "Job",
            "LaneExecutor",
            "DeliveryFacts",
            "PartialStream",
            "QueueDelay",
            "ServerKind",
            "SignedSeal",
            "SpillStorage",
            "StackedTokenBackend",
            "StorageTerms",
            "DeliveryPolicy",
            "ServerProbe",
            "StreamingPageResponse",
            "SubjectBinding",
            "ThreadState",
            "TokenRejection",
            "TokenSeal",
            "TokenStorage",
            "ServerFacts",
            "boundary_job_finished",
            "boundary_job_started",
            "boundary_settled",
            "boundary_state_written",
            "boundary_token_rejected",
            "defer_backend_loaded",
            "page_stream_closed",
            "page_stream_declined",
            "page_stream_opened",
            "remaining",
        }
    ),
    "next.testing": frozenset(
        {
            "AsyncNextClient",
            "FrozenClock",
            "MemoryPort",
            "Navigation",
            "NextClient",
            "NoSeal",
            "PartialEnvelope",
            "PublishedMessages",
            "ReloadedBoundary",
            "SignalEvent",
            "SignalRecorder",
            "SitemapUrl",
            "SocketTicket",
            "SseEvent",
            "SseReader",
            "StaticCollectorProxy",
            "StreamedChunk",
            "StreamedPage",
            "WebSocketCommunicator",
            "aissue_socket_ticket",
            "assert_has_class",
            "assert_metadata",
            "assert_missing_class",
            "aread_stream",
            "boundary_errors",
            "boundary_gate",
            "build_form_for",
            "capture_framework_signals",
            "capture_published",
            "capture_signals",
            "clear_loaded_dirs",
            "declared_layouts",
            "eager_load_components",
            "eager_load_pages",
            "envelope_of",
            "expire_boundary_token",
            "find_anchor",
            "find_form",
            "form_action",
            "form_fields",
            "freeze_token_clock",
            "hidden_fields",
            "init_payload",
            "issue_boundary_token",
            "issue_socket_ticket",
            "make_resolution_context",
            "memory_broker",
            "override_component_backends",
            "override_dependency",
            "override_form_action",
            "override_next_settings",
            "override_provider",
            "override_websocket_mount",
            "parallel_boundaries",
            "parse_sitemap",
            "patch_static_collector",
            "read_stream",
            "render_component_by_name",
            "render_page",
            "reset_check_caches",
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
            "sse_events",
        }
    ),
    "next.transactions": frozenset(
        {
            "AtomicMode",
            "AtomicOverride",
            "AtomicScope",
            "TransactionRule",
            "atomic_scope",
            "request_atomic",
            "skip_atomic_requests",
        }
    ),
    "next.views": frozenset({"page_view"}),
}

# Class names two areas both export, each with why both stay. The list only shrinks.
TWINS: dict[str, str] = {
    "Position": "a realtime topic sequence and a component walk, both shipped before",
    "Route": "a realtime handler binding and a routed page, both shipped before",
    "Script": "a Redis broker script and a page script, both shipped before",
}


def _exported(tree: ast.Module) -> set[str]:
    """Return the names a module lists in its literal `__all__`."""
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, ast.List | ast.Tuple)
        ):
            return {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    return set()


def exported_classes() -> dict[str, frozenset[str]]:
    """Map each class a module of `next/` defines and exports to the areas doing so."""
    root = package_root()
    areas: dict[str, set[str]] = {}
    for file in sorted(root.rglob("*.py")):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        defined = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        area = file.relative_to(root).with_suffix("").parts[0]
        for name in defined & _exported(tree):
            areas.setdefault(name, set()).add(area)
    return {name: frozenset(found) for name, found in areas.items()}


def twin_report(found: dict[str, frozenset[str]], known: dict[str, str]) -> list[str]:
    """Name every class two areas export and every known twin that no longer is one."""
    twins = {name for name, areas in found.items() if len(areas) > 1}
    new = [
        f"{name} is exported by {', '.join(sorted(found[name]))}, rename one of them."
        for name in sorted(twins - known.keys())
    ]
    gone = [
        f"{name} is no longer a twin, so delete it from TWINS in tests/test_public_api.py."
        for name in sorted(known.keys() - twins)
    ]
    return [*new, *gone]


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


class TestNextClientSurface:
    """The test client reloads a boundary by a minted or by a rendered token."""

    @pytest.mark.parametrize(
        "method", ["boundary_token", "follow_boundary", "get_boundary"]
    )
    def test_the_boundary_helpers_are_public(self, method: str) -> None:
        assert callable(getattr(NextClient, method))


class TestExportedTwins:
    """No class name is exported by two areas of `next/` outside the known twins."""

    def test_the_scan_reads_the_exports(self) -> None:
        found = exported_classes()
        assert found["Route"] == {"realtime", "urls"}
        assert found["Source"] == {"caches"}

    def test_no_two_areas_export_one_class_name(self) -> None:
        assert twin_report(exported_classes(), TWINS) == []

    def test_a_new_twin_is_named(self) -> None:
        found = {"Plan": frozenset({"defer", "partial"})}
        assert twin_report(found, {}) == [
            "Plan is exported by defer, partial, rename one of them."
        ]

    def test_a_known_twin_that_is_gone_is_named(self) -> None:
        found = {"Plan": frozenset({"partial"})}
        stale = (
            "Plan is no longer a twin, so delete it from TWINS in "
            "tests/test_public_api.py."
        )
        assert twin_report(found, {"Plan": "two plans"}) == [stale]
