from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.checks.registry import registry as check_registry
from django.test import override_settings

from next.checks import reset_check_caches
from next.components import ComponentInfo, FileComponentsBackend
from next.forms.backends import FormActionBackend, RegistryFormActionBackend
from next.pages.checks import composed
from next.partial import checks
from next.partial.registry import BUILTIN_OPS, register_patch_op
from tests.support import (
    PARTIAL_ROUTER_MANAGER_TARGETS,
    RootPagesRouter,
    patch_checks_router_manager_with_routers,
    patched_router_manager,
)


@contextmanager
def _scanned_root(root: Path) -> Iterator[None]:
    """Point the page-scanning checks at one on-disk page tree."""
    manager = MagicMock()
    manager.backends = (MagicMock(),)
    with (
        patched_router_manager(*PARTIAL_ROUTER_MANAGER_TARGETS, manager=manager),
        patch("next.discovery.get_pages_directories", return_value=[root]),
    ):
        yield


@contextmanager
def _context_pages(*pages: tuple[Path, str, str]) -> Iterator[None]:
    """Point the page-scanning checks at real on-disk page directories.

    Real imports and compiles mimic production, so a zone tag and a
    `@context` registration land here the same way they do live.
    """
    root = pages[0][0].parent.parent
    for page_file, source, body in pages:
        page_file.write_text(source)
        (page_file.parent / "template.djx").write_text(body)

    with _scanned_root(root):
        yield


@contextmanager
def _composed_pages(*pages: tuple[Path, str]) -> Iterator[None]:
    """Point the checks at pages whose `page.py` registers nothing."""
    with _context_pages(*((page_file, "x = 1", body) for page_file, body in pages)):
        yield


def _page_dir(tmp_path: Path, name: str) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory / "page.py"


class TestDuplicateZoneCheck:
    """`next.E060` fires when two zones in one page share a name."""

    def test_duplicate_name_in_one_page(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "dup")
        body = (
            '{% zone "side" %}<p>{{ a }}</p>{% endzone %}'
            '{% zone "side" %}<p>{{ b }}</p>{% endzone %}'
        )
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_duplicate_zone_names()]
        assert ids == [checks.E_DUPLICATE_ZONE]

    def test_distinct_names_are_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "ok")
        body = (
            '{% zone "left" %}<p>{{ a }}</p>{% endzone %}'
            '{% zone "right" %}<p>{{ b }}</p>{% endzone %}'
        )
        with _composed_pages((page_file, body)):
            assert checks.check_duplicate_zone_names() == []


class TestZoneSlugCheck:
    """`next.E061` fires when a zone name is not an ASCII slug."""

    def test_non_ascii_name(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "slug")
        with _composed_pages(
            (page_file, '{% zone "зона" %}<p>{{ a }}</p>{% endzone %}')
        ):
            ids = [m.id for m in checks.check_zone_name_is_slug()]
        assert ids == [checks.E_NON_ASCII_ZONE]

    def test_ascii_slug_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "slug_ok")
        with _composed_pages(
            (page_file, '{% zone "side-bar_2" %}<p>{{ a }}</p>{% endzone %}')
        ):
            assert checks.check_zone_name_is_slug() == []


class TestZoneInLoopCheck:
    """`next.E062` fires when a zone sits inside a `{% for %}`."""

    def test_zone_in_for(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "loop")
        body = (
            "{% for item in items %}"
            '{% zone "row" %}<p>{{ item }}</p>{% endzone %}'
            "{% endfor %}"
        )
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_zone_not_in_loop()]
        assert ids == [checks.E_ZONE_IN_FOR]

    def test_zone_outside_loop_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "no_loop")
        body = '{% zone "row" %}<p>{{ a }}</p>{% endzone %}'
        with _composed_pages((page_file, body)):
            assert checks.check_zone_not_in_loop() == []


class TestZoneInIfCheck:
    """`next.E063` fires when a zone sits inside an `{% if %}`, either branch."""

    def test_zone_in_if_branch(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "if_then")
        body = '{% if cond %}{% zone "guard" %}<p>{{ a }}</p>{% endzone %}{% endif %}'
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_zone_not_in_if()]
        assert ids == [checks.E_ZONE_IN_IF]

    def test_zone_in_else_branch(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "if_else")
        body = (
            "{% if cond %}plain"
            '{% else %}{% zone "guard" %}<p>{{ a }}</p>{% endzone %}'
            "{% endif %}"
        )
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_zone_not_in_if()]
        assert ids == [checks.E_ZONE_IN_IF]

    def test_zone_outside_if_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "no_if")
        body = '{% zone "guard" %}{% if cond %}<p>{{ a }}</p>{% endif %}{% endzone %}'
        with _composed_pages((page_file, body)):
            assert checks.check_zone_not_in_if() == []


class TestLazyPlaceholderCheck:
    """`next.E064` fires when a lazy zone has no `{% placeholder %}`."""

    def test_lazy_without_placeholder(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "lazy_bad")
        body = '{% zone "z" lazy="load" %}<p>{{ a }}</p>{% endzone %}'
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_lazy_zone_has_placeholder()]
        assert ids == [checks.E_LAZY_WITHOUT_PLACEHOLDER]

    def test_lazy_with_placeholder_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "lazy_ok")
        body = (
            '{% zone "z" lazy="load" %}<p>{{ a }}</p>'
            "{% placeholder %}<div>loading</div>{% endzone %}"
        )
        with _composed_pages((page_file, body)):
            assert checks.check_lazy_zone_has_placeholder() == []


class TestComposedTemplateCompileCheck:
    """`next.E072` fires when a composed page template fails to compile."""

    def test_placeholder_without_lazy_errors(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "broken")
        body = '{% zone "z" %}<p>{{ a }}</p>{% placeholder %}<p>p</p>{% endzone %}'
        with _composed_pages((page_file, body)):
            messages = checks.check_composed_templates_compile()
        assert [m.id for m in messages] == [checks.E_COMPOSED_TEMPLATE_SYNTAX]
        assert str(page_file) in messages[0].msg
        assert "placeholder requires lazy=" in messages[0].msg

    def test_healthy_page_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "compiles")
        body = '{% zone "z" %}<p>{{ a }}</p>{% endzone %}'
        with _composed_pages((page_file, body)):
            assert checks.check_composed_templates_compile() == []

    def test_only_the_broken_page_is_reported(self, tmp_path: Path) -> None:
        healthy = _page_dir(tmp_path, "fine")
        broken = _page_dir(tmp_path, "torn")
        with _composed_pages(
            (healthy, '{% zone "ok" %}<p>{{ a }}</p>{% endzone %}'),
            (broken, '{% zone "z" %}b{% placeholder %}p{% endzone %}'),
        ):
            messages = checks.check_composed_templates_compile()
        assert [m.id for m in messages] == [checks.E_COMPOSED_TEMPLATE_SYNTAX]
        assert messages[0].obj == str(broken)


class TestComposedTemplateCompileIsDeployOnly:
    """`next.E072` compiles the whole tree, so only `check --deploy` runs it."""

    def test_absent_from_the_default_registry(self) -> None:
        assert (
            checks.check_composed_templates_compile not in check_registry.get_checks()
        )

    def test_present_once_deployment_checks_are_asked_for(self) -> None:
        registered = check_registry.get_checks(include_deployment_checks=True)
        assert checks.check_composed_templates_compile in registered


class TestWithOverZoneCheck:
    """`next.W067` warns when a `{% with %}` wraps a zone directly."""

    def test_with_directly_over_zone(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "with_bad")
        body = (
            '{% with greeting="hi" %}'
            '{% zone "z" %}<p>{{ greeting }}</p>{% endzone %}'
            "{% endwith %}"
        )
        with _composed_pages((page_file, body)):
            messages = checks.check_with_directly_over_zone()
        assert [m.id for m in messages] == [checks.W_WITH_OVER_ZONE]

    def test_zone_without_with_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "with_ok")
        body = '{% zone "z" %}{% with greeting="hi" %}{{ greeting }}{% endwith %}{% endzone %}'
        with _composed_pages((page_file, body)):
            assert checks.check_with_directly_over_zone() == []


class TestContextZoneNameCheck:
    """`next.E078` fires when a `@context(zone=)` names an undeclared zone."""

    def test_undeclared_zone_name(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "zone_unknown")
        source = (
            "from next.pages import page\n\n"
            "@page.context('rows', zone='ledger')\n"
            "def rows():\n"
            "    return []\n"
        )
        body = '{% zone "table" %}<p>{{ rows }}</p>{% endzone %}'
        with _context_pages((page_file, source, body)):
            messages = checks.check_context_zone_names_exist()
        assert [m.id for m in messages] == [checks.E_CONTEXT_ZONE_UNKNOWN]
        assert 'rows (key "rows")' in messages[0].msg
        assert '"ledger"' in messages[0].msg
        assert "Declared zones: 'table'." in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_declared_zone_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "zone_ok")
        source = (
            "from next.pages import page\n\n"
            "@page.context('rows', zone='table')\n"
            "def rows():\n"
            "    return []\n"
        )
        body = '{% zone "table" %}<p>{{ rows }}</p>{% endzone %}'
        with _context_pages((page_file, source, body)):
            assert checks.check_context_zone_names_exist() == []

    def test_nested_zone_counts_as_declared(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "zone_nested")
        source = (
            "from next.pages import page\n\n"
            "@page.context('rows', zone='inner')\n"
            "def rows():\n"
            "    return []\n"
        )
        body = (
            '{% zone "outer" %}<p>{{ rows }}</p>'
            '{% zone "inner" %}<b>{{ rows }}</b>{% endzone %}'
            "{% endzone %}"
        )
        with _context_pages((page_file, source, body)):
            assert checks.check_context_zone_names_exist() == []

    def test_context_without_a_zone_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "zone_less")
        source = (
            "from next.pages import page\n\n"
            "@page.context('rows')\n"
            "def rows():\n"
            "    return []\n"
        )
        body = '{% zone "table" %}<p>{{ rows }}</p>{% endzone %}'
        with _context_pages((page_file, source, body)):
            assert checks.check_context_zone_names_exist() == []

    def test_keyless_context_on_a_page_without_zones(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "zone_none")
        source = (
            "from next.pages import page\n\n"
            "@page.context(zone='table')\n"
            "def merged() -> dict:\n"
            "    return {}\n"
        )
        with _context_pages((page_file, source, "<p>{{ a }}</p>")):
            messages = checks.check_context_zone_names_exist()
        assert [m.id for m in messages] == [checks.E_CONTEXT_ZONE_UNKNOWN]
        assert "The @context merged in" in messages[0].msg
        assert "The page declares no zones." in messages[0].msg


class TestRepeatedFormKeyCheck:
    """`next.W070` warns when a looped `{% form %}` has no key or zone."""

    def test_form_in_for_without_key_or_zone(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "rows_bad")
        body = (
            "{% for item in items %}"
            '{% form "rename_item" %}<input name="title">{% endform %}'
            "{% endfor %}"
        )
        with _composed_pages((page_file, body)):
            ids = [m.id for m in checks.check_repeated_form_has_key()]
        assert ids == [checks.W_FORM_IN_FOR_NO_KEY]

    def test_form_in_for_with_key_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "rows_key")
        body = (
            "{% for item in items %}"
            '{% form "rename_item" key=item.pk %}<input name="title">{% endform %}'
            "{% endfor %}"
        )
        with _composed_pages((page_file, body)):
            assert checks.check_repeated_form_has_key() == []

    def test_form_in_for_with_zone_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "rows_zone")
        body = (
            "{% for item in items %}"
            '{% form "rename_item" zone="rows" %}<input name="title">{% endform %}'
            "{% endfor %}"
        )
        with _composed_pages((page_file, body)):
            assert checks.check_repeated_form_has_key() == []

    def test_form_outside_loop_is_silent(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "rows_single")
        body = '{% form "rename_item" %}<input name="title">{% endform %}'
        with _composed_pages((page_file, body)):
            assert checks.check_repeated_form_has_key() == []


@contextmanager
def _component(template_path: Path | None) -> Generator[None, None, None]:
    """Point `next.E065` at a fake components manager with one component."""
    backend = FileComponentsBackend({"DIRS": [], "COMPONENTS_DIR": "_components"})
    if template_path is not None:
        backend._registry.register(
            ComponentInfo(
                name="card",
                scope_root=template_path.parent,
                scope_relative="",
                template_path=template_path,
                module_path=None,
                is_simple=True,
            )
        )
    backend._loaded = True
    manager = MagicMock()
    manager.backends = (backend,)

    settings_ns = MagicMock()
    settings_ns.COMPONENT_BACKENDS = [
        {"BACKEND": "next.components.FileComponentsBackend"}
    ]
    with (
        patch("next.partial.checks.zones.next_framework_settings", settings_ns),
        patch("next.partial.checks.zones.get_components_manager", return_value=manager),
    ):
        yield


class TestZoneInComponentCheck:
    """`next.E065` fires when a component template declares a zone."""

    def test_zone_in_component_template(self, tmp_path: Path) -> None:
        component = tmp_path / "card.djx"
        component.write_text('<div>{% zone "inner" %}x{% endzone %}</div>')
        with _component(component):
            ids = [m.id for m in checks.check_no_zone_in_component()]
        assert ids == [checks.E_ZONE_IN_COMPONENT]

    def test_component_without_zone_is_silent(self, tmp_path: Path) -> None:
        component = tmp_path / "card.djx"
        component.write_text("<div>plain card</div>")
        with _component(component):
            assert checks.check_no_zone_in_component() == []

    def test_no_component_backends_is_silent(self) -> None:
        settings_ns = MagicMock()
        settings_ns.COMPONENT_BACKENDS = []
        with patch("next.partial.checks.zones.next_framework_settings", settings_ns):
            assert checks.check_no_zone_in_component() == []


@pytest.mark.usefixtures("restored_op_registry")
class TestCustomPatchOpCheck:
    """A custom verb that shadows a built-in or is malformed is reported."""

    def test_default_registry_is_silent(self) -> None:
        assert checks.check_custom_patch_ops_well_formed() == []

    def test_well_formed_custom_verb_is_silent(self) -> None:
        register_patch_op("confetti")
        assert checks.check_custom_patch_ops_well_formed() == []

    @pytest.mark.parametrize("verb", sorted(BUILTIN_OPS))
    def test_shadowing_a_builtin_verb_errors(self, verb: str) -> None:
        # a custom op named after a built-in verb never runs, the built-in wins
        register_patch_op(verb)
        messages = checks.check_custom_patch_ops_well_formed()
        assert [m.id for m in messages] == [checks.E_OP_SHADOWS_BUILTIN]
        assert "shadows a built-in verb" in messages[0].msg

    def test_malformed_verb_token_errors(self) -> None:
        register_patch_op("not a token")
        messages = checks.check_custom_patch_ops_well_formed()
        assert [m.id for m in messages] == [checks.E_OP_BAD_NAME]
        assert "is not a valid verb token" in messages[0].msg

    def test_a_shadowed_builtin_is_not_also_reported_as_malformed(self) -> None:
        # "morph" is a valid token, so the shadow branch has to end the verdict
        register_patch_op("morph")
        assert len(checks.check_custom_patch_ops_well_formed()) == 1


class _PartialUnawareBackend(RegistryFormActionBackend):
    """Backend whose shape_response override never routes partial requests."""

    def shape_response(self, request: object, outcome: object) -> object:
        """Serve a full page regardless of the partial switch."""
        del request, outcome
        return object()


@contextmanager
def _form_backends(*backends, partial_active: bool) -> Iterator[None]:
    """Point the W068 check at given form backends and partial-config state."""
    manager = MagicMock()
    manager.backends = tuple(backends)
    settings_ns = MagicMock()
    settings_ns.PARTIAL_BACKENDS = (
        [{"BACKEND": "next.partial.JsonPartialProtocolBackend"}]
        if partial_active
        else []
    )
    with (
        patch("next.partial.checks.forms.form_action_manager", manager),
        patch("next.partial.checks.backends.next_framework_settings", settings_ns),
    ):
        yield


class TestFormBackendPartialAwareCheck:
    """`next.W068` fires on a partial-unaware custom form backend."""

    def test_default_backend_is_silent(self) -> None:
        with _form_backends(RegistryFormActionBackend(), partial_active=True):
            assert checks.check_form_backend_partial_aware() == []

    def test_unaware_override_warns(self) -> None:
        with _form_backends(_PartialUnawareBackend(), partial_active=True):
            ids = [m.id for m in checks.check_form_backend_partial_aware()]
        assert ids == [checks.W_FORM_BACKEND_NOT_AWARE]

    def test_silent_when_partial_backends_inactive(self) -> None:
        with _form_backends(_PartialUnawareBackend(), partial_active=False):
            assert checks.check_form_backend_partial_aware() == []

    def test_inherited_shape_response_is_silent(self) -> None:
        # a subclass that does not override shape_response inherits the
        # partial-aware base method, so the check stays quiet
        assert _PartialUnawareBackend.shape_response is not (
            FormActionBackend.shape_response
        )
        assert RegistryFormActionBackend.shape_response is (
            FormActionBackend.shape_response
        )


_MANIFEST_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
_PLAIN_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"


@contextmanager
def _partial_options(
    options: object, *, storage: str = _PLAIN_STORAGE
) -> Iterator[None]:
    """Run the W069 check against one backend entry and a staticfiles storage."""
    config = {"BACKEND": "next.partial.JsonPartialProtocolBackend", "OPTIONS": options}
    with override_settings(
        NEXT_FRAMEWORK={"PARTIAL_BACKENDS": [config]},
        STORAGES={"staticfiles": {"BACKEND": storage}},
    ):
        yield


class TestManifestVersionStorageCheck:
    """`next.W069` fires when manifest versioning has no manifest storage."""

    def test_sentinel_without_manifest_storage_warns(self) -> None:
        with _partial_options({"VERSION": "manifest"}):
            ids = [m.id for m in checks.check_manifest_version_has_manifest_storage()]
        assert ids == [checks.W_MANIFEST_VERSION_NO_STORAGE]

    def test_manifest_storage_is_silent(self) -> None:
        with _partial_options({"VERSION": "manifest"}, storage=_MANIFEST_STORAGE):
            assert checks.check_manifest_version_has_manifest_storage() == []

    def test_stock_project_is_silent(self) -> None:
        # a project on the framework defaults never asked for the manifest, so the
        # plain storage of a fresh startproject contradicts nothing
        with override_settings(
            NEXT_FRAMEWORK={}, STORAGES={"staticfiles": {"BACKEND": _PLAIN_STORAGE}}
        ):
            assert checks.check_manifest_version_has_manifest_storage() == []

    @pytest.mark.parametrize(
        "options",
        [{}, {"VERSION": None}, {"VERSION": "release-7"}, "not-a-mapping"],
        ids=["no_version_key", "derived_version", "pinned_version", "bad_options"],
    )
    def test_without_the_sentinel_is_silent(self, options: object) -> None:
        with _partial_options(options):
            assert checks.check_manifest_version_has_manifest_storage() == []

    @pytest.mark.parametrize(
        "configs", [[], ["not-a-mapping"]], ids=["no_entries", "non_dict_entry"]
    )
    def test_silent_without_a_usable_backend_entry(self, configs: object) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"PARTIAL_BACKENDS": configs},
            STORAGES={"staticfiles": {"BACKEND": _PLAIN_STORAGE}},
        ):
            assert checks.check_manifest_version_has_manifest_storage() == []


class TestAssetVersionMovesBetweenDeploysCheck:
    """`next.W083` fires when nothing can move the derived asset version."""

    def test_derived_version_without_a_source_warns(self) -> None:
        with _partial_options({"VERSION": None}):
            ids = [m.id for m in checks.check_asset_version_moves_between_deploys()]
        assert ids == [checks.W_ASSET_VERSION_FROZEN]

    def test_stock_defaults_warn(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={}, STORAGES={"staticfiles": {"BACKEND": _PLAIN_STORAGE}}
        ):
            ids = [m.id for m in checks.check_asset_version_moves_between_deploys()]
        assert ids == [checks.W_ASSET_VERSION_FROZEN]

    def test_project_static_version_is_silent(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"STATIC_VERSION": "build-42"},
            STORAGES={"staticfiles": {"BACKEND": _PLAIN_STORAGE}},
        ):
            assert checks.check_asset_version_moves_between_deploys() == []

    def test_manifest_storage_is_silent(self) -> None:
        with _partial_options({"VERSION": None}, storage=_MANIFEST_STORAGE):
            assert checks.check_asset_version_moves_between_deploys() == []

    @pytest.mark.parametrize(
        "options",
        [{"VERSION": "release-7"}, {"VERSION": "manifest"}],
        ids=["pinned_tag", "manifest_sentinel"],
    )
    def test_a_named_version_is_silent(self, options: dict[str, object]) -> None:
        # a pinned tag moves by hand and the sentinel answers to next.W069, so
        # neither case earns a second warning about the same decision
        with _partial_options(options):
            assert checks.check_asset_version_moves_between_deploys() == []

    @pytest.mark.parametrize(
        "configs", [[], ["not-a-mapping"]], ids=["no_entries", "non_dict_entry"]
    )
    def test_silent_without_a_usable_backend_entry(self, configs: object) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"PARTIAL_BACKENDS": configs},
            STORAGES={"staticfiles": {"BACKEND": _PLAIN_STORAGE}},
        ):
            assert checks.check_asset_version_moves_between_deploys() == []


class TestAssetVersionCheckIsDeployOnly:
    """`next.W083` describes a development checkout, so only `--deploy` runs it."""

    def test_absent_from_the_default_registry(self) -> None:
        registered = check_registry.get_checks()
        assert checks.check_asset_version_moves_between_deploys not in registered

    def test_present_once_deployment_checks_are_asked_for(self) -> None:
        registered = check_registry.get_checks(include_deployment_checks=True)
        assert checks.check_asset_version_moves_between_deploys in registered


@contextmanager
def _partial_backends(configs: object) -> Iterator[None]:
    """Point the W071 check at the given PARTIAL_BACKENDS config value."""
    settings_ns = MagicMock()
    settings_ns.PARTIAL_BACKENDS = configs
    with patch("next.partial.checks.backends.next_framework_settings", settings_ns):
        yield


_BACKEND_DICT = {"BACKEND": "next.partial.JsonPartialProtocolBackend"}


class TestSinglePartialBackendCheck:
    """`next.W071` warns on more than one valid PARTIAL_BACKENDS entry."""

    def test_two_valid_dicts_warn(self) -> None:
        with _partial_backends([_BACKEND_DICT, dict(_BACKEND_DICT)]):
            ids = [m.id for m in checks.check_single_partial_backend()]
        assert ids == [checks.W_TOO_MANY_BACKENDS]

    def test_one_dict_is_silent(self) -> None:
        with _partial_backends([_BACKEND_DICT]):
            assert checks.check_single_partial_backend() == []

    def test_no_backends_is_silent(self) -> None:
        with _partial_backends([]):
            assert checks.check_single_partial_backend() == []

    def test_only_invalid_entries_are_silent(self) -> None:
        with _partial_backends([1, "x"]):
            assert checks.check_single_partial_backend() == []

    def test_one_dict_one_non_dict_is_silent(self) -> None:
        with _partial_backends([_BACKEND_DICT, "x"]):
            assert checks.check_single_partial_backend() == []

    def test_non_list_config_is_silent(self) -> None:
        with _partial_backends("not-a-list"):
            assert checks.check_single_partial_backend() == []


class TestBackendNamesPathCheck:
    """`next.E073` fires when a PARTIAL_BACKENDS entry omits its BACKEND key."""

    @pytest.mark.parametrize(
        "config",
        [[{}], ["x", {}], ({},)],
        ids=["bare_entry", "non_dict_skipped", "tuple_config"],
    )
    def test_entry_without_backend_key_errors(self, config: object) -> None:
        with _partial_backends(config):
            ids = [m.id for m in checks.check_partial_backend_names_a_path()]
        assert ids == [checks.E_BACKEND_WITHOUT_PATH]

    @pytest.mark.parametrize(
        "config", [[_BACKEND_DICT], "not-a-list"], ids=["valid_entry", "non_sequence"]
    )
    def test_valid_or_non_sequence_config_is_silent(self, config: object) -> None:
        with _partial_backends(config):
            assert checks.check_partial_backend_names_a_path() == []


class TestBackendsShapeCheck:
    """`next.E067` fires when PARTIAL_BACKENDS holds anything but a list."""

    @pytest.mark.parametrize(
        "configs",
        [(_BACKEND_DICT,), _BACKEND_DICT, "next.partial.JsonPartialProtocolBackend"],
        ids=["tuple", "bare_dict", "dotted_path"],
    )
    def test_non_list_value_errors(self, configs: object) -> None:
        with override_settings(NEXT_FRAMEWORK={"PARTIAL_BACKENDS": configs}):
            ids = [m.id for m in checks.check_partial_backends_is_a_list()]
        assert ids == [checks.E_BACKENDS_NOT_A_LIST]

    @pytest.mark.parametrize(
        "framework",
        [{"PARTIAL_BACKENDS": [_BACKEND_DICT]}, {"PARTIAL_BACKENDS": []}, {}, "broken"],
        ids=["list_entry", "empty_list", "key_absent", "settings_not_a_dict"],
    )
    def test_list_or_absent_value_is_silent(self, framework: object) -> None:
        with override_settings(NEXT_FRAMEWORK=framework):
            assert checks.check_partial_backends_is_a_list() == []


class TestThirdPartyBackendPagesReachTheZoneChecks:
    """A backend that only reports its trees has its zones checked too."""

    def test_duplicate_zone_names_in_a_reported_tree(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "dup")
        page_file.write_text("x = 1")
        (page_file.parent / "template.djx").write_text(
            '{% zone "side" %}<p>{{ a }}</p>{% endzone %}'
            '{% zone "side" %}<p>{{ b }}</p>{% endzone %}'
        )

        with patch_checks_router_manager_with_routers(
            routers=[RootPagesRouter([tmp_path])]
        ):
            ids = [m.id for m in checks.check_duplicate_zone_names()]

        assert ids == [checks.E_DUPLICATE_ZONE]


class TestChecksSilentOnValidComposite:
    """A page with well-formed zones triggers none of the zone checks."""

    def test_all_page_checks_clear(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "valid")
        body = (
            '{% zone "header" %}<h1>{{ title }}</h1>{% endzone %}'
            '{% zone "feed" lazy="revealed" %}<ul>{{ items }}</ul>'
            "{% placeholder %}<p>loading</p>{% endzone %}"
        )
        with _composed_pages((page_file, body)):
            assert checks.check_duplicate_zone_names() == []
            assert checks.check_zone_name_is_slug() == []
            assert checks.check_zone_not_in_loop() == []
            assert checks.check_zone_not_in_if() == []
            assert checks.check_lazy_zone_has_placeholder() == []
            assert checks.check_with_directly_over_zone() == []


_ZONE_CHECKS = (
    checks.check_duplicate_zone_names,
    checks.check_zone_name_is_slug,
    checks.check_zone_not_in_loop,
    checks.check_zone_not_in_if,
    checks.check_repeated_form_has_key,
    checks.check_lazy_zone_has_placeholder,
    checks.check_with_directly_over_zone,
)


@contextmanager
def _counting_collect() -> Iterator[list[int]]:
    """Count calls to the composed-page collector, delegating to the real one."""
    real = composed._collect_composed_pages
    calls = [0]

    def counting(manager: object) -> Iterator[tuple[Path, object]]:
        calls[0] += 1
        return real(manager)

    with patch.object(composed, "_collect_composed_pages", side_effect=counting):
        yield calls


class TestComposedPagesMemo:
    """The seven zone checks share one walk of the composed-page tree."""

    def test_seven_checks_collect_pages_once(self, tmp_path: Path) -> None:
        page_file = _page_dir(tmp_path, "shared")
        body = '{% zone "z" %}<p>{{ a }}</p>{% endzone %}'
        with _composed_pages((page_file, body)), _counting_collect() as calls:
            for check in _ZONE_CHECKS:
                check()
        assert calls[0] == 1

    def test_new_manager_invalidates_memo(self, tmp_path: Path) -> None:
        first_page = _page_dir(tmp_path, "one")
        with _composed_pages(
            (first_page, '{% zone "solo" %}<p>{{ a }}</p>{% endzone %}')
        ):
            assert checks.check_duplicate_zone_names() == []
        second_page = _page_dir(tmp_path, "two")
        dup = '{% zone "x" %}a{% endzone %}{% zone "x" %}b{% endzone %}'
        with _composed_pages((second_page, dup)):
            ids = [m.id for m in checks.check_duplicate_zone_names()]
        assert ids == [checks.E_DUPLICATE_ZONE]

    def test_a_check_cache_reset_recollects_on_a_live_manager(
        self, tmp_path: Path
    ) -> None:
        page_file = _page_dir(tmp_path, "live")
        body = '{% zone "z" %}<p>{{ a }}</p>{% endzone %}'
        with _composed_pages((page_file, body)), _counting_collect() as calls:
            checks.check_duplicate_zone_names()
            checks.check_zone_name_is_slug()
            assert calls[0] == 1
            reset_check_caches()
            checks.check_zone_not_in_loop()
            assert calls[0] == 2

    def test_memo_does_not_hide_e072(self, tmp_path: Path) -> None:
        broken = _page_dir(tmp_path, "torn")
        body = '{% zone "z" %}b{% placeholder %}p{% endzone %}'
        with _composed_pages((broken, body)):
            checks.check_duplicate_zone_names()
            messages = checks.check_composed_templates_compile()
        assert [m.id for m in messages] == [checks.E_COMPOSED_TEMPLATE_SYNTAX]
