from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from next.components import ComponentInfo, FileComponentsBackend
from next.forms.backends import FormActionBackend, RegistryFormActionBackend
from next.partial import checks
from next.partial.registry import patch_op_registry, register_patch_op


@contextmanager
def _composed_pages(*pages: tuple[Path, str]) -> Iterator[None]:
    """Point the page-scanning checks at real on-disk page directories.

    Each entry is a `page.py` path and the `template.djx` body next to it.
    The global page instance compiles the body through its layout loader, so
    a zone tag in the body is discovered exactly as it is in production.
    """
    routes: list[tuple[str, Path]] = []
    root = pages[0][0].parent if pages else Path()
    for page_file, body in pages:
        page_file.write_text("x = 1")
        (page_file.parent / "template.djx").write_text(body)
        routes.append((f"/{page_file.parent.name}/", page_file))

    manager = MagicMock()
    router = MagicMock()
    manager._backends = [router]
    router._scan_pages_directory.return_value = routes
    with (
        patch("next.partial.checks.get_router_manager", return_value=(manager, [])),
        patch("next.checks.common.get_pages_directory", return_value=root),
    ):
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
    manager._reload_config = lambda: None
    manager._backends = [backend]

    settings_ns = MagicMock()
    settings_ns.COMPONENT_BACKENDS = [
        {"BACKEND": "next.components.FileComponentsBackend"}
    ]
    with (
        patch("next.partial.checks.next_framework_settings", settings_ns),
        patch("next.partial.checks.ComponentsManager", return_value=manager),
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
        with patch("next.partial.checks.next_framework_settings", settings_ns):
            assert checks.check_no_zone_in_component() == []


@pytest.fixture()
def restore_op_registry():
    """Snapshot and restore the patch-op registry around a test."""
    ops = set(patch_op_registry._ops)
    custom = set(patch_op_registry._custom)
    yield
    patch_op_registry._ops = ops
    patch_op_registry._custom = custom


@pytest.mark.usefixtures("restore_op_registry")
class TestUnregisteredOpCheck:
    """`next.E066` fires on a custom verb that shadows or is malformed."""

    def test_default_registry_is_silent(self) -> None:
        assert checks.check_custom_patch_ops_well_formed() == []

    def test_well_formed_custom_verb_is_silent(self) -> None:
        register_patch_op("confetti")
        assert checks.check_custom_patch_ops_well_formed() == []

    def test_shadowing_a_builtin_verb_errors(self) -> None:
        # a custom op named after a built-in verb never runs, the built-in wins
        patch_op_registry._custom.add("morph")
        ids = [m.id for m in checks.check_custom_patch_ops_well_formed()]
        assert ids == [checks.E_UNREGISTERED_OP]

    def test_malformed_verb_token_errors(self) -> None:
        register_patch_op("not a token")
        ids = [m.id for m in checks.check_custom_patch_ops_well_formed()]
        assert ids == [checks.E_UNREGISTERED_OP]


class _PartialUnawareBackend(RegistryFormActionBackend):
    """Backend whose shape_response override never routes partial requests."""

    def shape_response(self, request: object, outcome: object) -> object:
        """Serve a full page regardless of the partial switch."""
        del request, outcome
        return object()


@contextmanager
def _form_backends(*backends: object, partial_active: bool) -> Iterator[None]:
    """Point the W068 check at given form backends and partial-config state."""
    manager = MagicMock()
    manager.backends = tuple(backends)
    settings_ns = MagicMock()
    settings_ns.PARTIAL_BACKENDS = (
        [{"BACKEND": "next.partial.PartialProtocolBackend"}] if partial_active else []
    )
    with (
        patch("next.partial.checks.form_action_manager", manager),
        patch("next.partial.checks.next_framework_settings", settings_ns),
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
def _partial_version(version: object) -> Iterator[None]:
    """Point the W069 check at a partial backend with the given VERSION option.

    A version of None means the OPTIONS mapping omits the key, so the check
    sees the implicit manifest sentinel.
    """
    options: dict[str, object] = {}
    if version is not None:
        options["VERSION"] = version
    settings_ns = MagicMock()
    settings_ns.PARTIAL_BACKENDS = [
        {"BACKEND": "next.partial.PartialProtocolBackend", "OPTIONS": options}
    ]
    with patch("next.partial.checks.next_framework_settings", settings_ns):
        yield


class TestManifestVersionStorageCheck:
    """`next.W069` fires when manifest versioning has no manifest storage."""

    def test_sentinel_without_manifest_storage_warns(self) -> None:
        with (
            _partial_version("manifest"),
            override_settings(STORAGES={"staticfiles": {"BACKEND": _PLAIN_STORAGE}}),
        ):
            ids = [m.id for m in checks.check_manifest_version_has_manifest_storage()]
        assert ids == [checks.W_MANIFEST_VERSION_NO_STORAGE]

    def test_implicit_sentinel_without_manifest_storage_warns(self) -> None:
        # an OPTIONS mapping with no VERSION key defaults to the manifest sentinel
        with (
            _partial_version(None),
            override_settings(STORAGES={"staticfiles": {"BACKEND": _PLAIN_STORAGE}}),
        ):
            ids = [m.id for m in checks.check_manifest_version_has_manifest_storage()]
        assert ids == [checks.W_MANIFEST_VERSION_NO_STORAGE]

    def test_legacy_storage_setting_without_manifest_warns(self) -> None:
        with (
            _partial_version("manifest"),
            override_settings(STORAGES={}, STATICFILES_STORAGE=_PLAIN_STORAGE),
        ):
            ids = [m.id for m in checks.check_manifest_version_has_manifest_storage()]
        assert ids == [checks.W_MANIFEST_VERSION_NO_STORAGE]

    def test_manifest_storage_is_silent(self) -> None:
        with (
            _partial_version("manifest"),
            override_settings(STORAGES={"staticfiles": {"BACKEND": _MANIFEST_STORAGE}}),
        ):
            assert checks.check_manifest_version_has_manifest_storage() == []

    def test_legacy_manifest_storage_is_silent(self) -> None:
        with (
            _partial_version("manifest"),
            override_settings(STORAGES={}, STATICFILES_STORAGE=_MANIFEST_STORAGE),
        ):
            assert checks.check_manifest_version_has_manifest_storage() == []

    def test_explicit_version_string_is_silent(self) -> None:
        # pinning VERSION to a literal string opts out of the manifest sentinel,
        # so the guard is live by other means and the warning never fires
        with (
            _partial_version("release-7"),
            override_settings(STORAGES={"staticfiles": {"BACKEND": _PLAIN_STORAGE}}),
        ):
            assert checks.check_manifest_version_has_manifest_storage() == []

    def test_silent_when_no_partial_backends(self) -> None:
        settings_ns = MagicMock()
        settings_ns.PARTIAL_BACKENDS = []
        with (
            patch("next.partial.checks.next_framework_settings", settings_ns),
            override_settings(STORAGES={"staticfiles": {"BACKEND": _PLAIN_STORAGE}}),
        ):
            assert checks.check_manifest_version_has_manifest_storage() == []


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
