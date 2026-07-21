from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.core.checks import Error, Warning as DjangoWarning
from django.core.checks.registry import registry
from django.test import override_settings

import next.pages.loaders as loaders_module
import next.static.checks as checks_module
from next.checks import reset_check_caches
from next.components import FileComponentsBackend
from next.static import KindRegistry
from next.static.checks import (
    check_asset_kinds_are_loadable,
    check_js_context_serializer,
    check_reserved_js_context_keys,
    check_static_backends,
)
from tests.support import patch_checks_router_manager


@pytest.fixture(autouse=True)
def _reset_check_caches():
    reset_check_caches()
    yield
    reset_check_caches()


def _ids(messages: list) -> list[str]:
    return [m.id for m in messages]


class TestEmptyConfig:
    def test_empty_list_emits_w030(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": []}):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.W030"]
        assert isinstance(messages[0], DjangoWarning)

    def test_non_list_falls_back_to_defaults(self) -> None:
        """Conf coerces non-list to defaults, so checks treat it as valid."""
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": "not-a-list"}):
            messages = check_static_backends(app_configs=None)
        assert messages == []


class TestValidConfig:
    def test_single_default_backend(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [{"BACKEND": "next.static.StaticFilesBackend"}]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert messages == []

    def test_valid_options_with_placeholders(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {
                            "css_tag": '<link href="{url}">',
                            "js_tag": '<script src="{url}"></script>',
                        },
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert messages == []


class TestBadEntries:
    def test_non_dict_entry_emits_e037(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": ["not-a-dict"]}):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.E037"]
        assert isinstance(messages[0], Error)

    def test_non_string_backend_emits_e037(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": 123}]}):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.E037"]

    def test_missing_module_emits_e036(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": "does.not.exist.Backend"}]}
        ):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.E036"]

    def test_not_subclass_emits_e037(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": "builtins.dict"}]}
        ):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.E037"]

    def test_duplicate_backend_emits_e038(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {"BACKEND": "next.static.StaticFilesBackend"},
                    {"BACKEND": "next.static.StaticFilesBackend"},
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.E038" in _ids(messages)


class TestOptionsWarnings:
    def test_css_tag_without_placeholder_emits_w031(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {"css_tag": "<link>"},
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.W031" in _ids(messages)

    def test_js_tag_without_placeholder_emits_w031(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {"js_tag": "<script></script>"},
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.W031" in _ids(messages)

    def test_non_string_tag_template_is_ignored(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {"css_tag": 42},
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.W031" not in _ids(messages)


class TestChecksRegistered:
    """System checks discovery picks up check_static_backends."""

    def test_registered_under_compatibility_tag(self) -> None:

        ids = {getattr(c, "__name__", None) for c in registry.registered_checks}
        assert "check_static_backends" in ids


class _NotASerializer:
    """Placeholder without a `dumps` method."""


class TestJsContextSerializerCheck:
    """check_js_context_serializer validates the configured dotted path."""

    def test_passes_when_unset(self) -> None:

        assert check_js_context_serializer() == []

    def test_passes_when_framework_setting_is_not_a_dict(self) -> None:

        with override_settings(NEXT_FRAMEWORK=["not a dict"]):
            assert check_js_context_serializer() == []

    def test_passes_for_default_json_serializer(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={
                "JS_CONTEXT_SERIALIZER": (
                    "next.static.serializers.JsonJsContextSerializer"
                )
            }
        ):
            assert check_js_context_serializer() == []

    def test_warns_on_non_string_value(self) -> None:

        with override_settings(NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": 42}):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W042"

    def test_warns_on_import_error(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": "tests.nonexistent.Missing"}
        ):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W042"
        assert "Cannot import" in messages[0].msg

    def test_warns_when_target_is_not_a_class(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={
                "JS_CONTEXT_SERIALIZER": "next.static.serializers.resolve_serializer"
            }
        ):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W042"
        assert "not a class" in messages[0].msg

    def test_warns_when_instance_fails_protocol(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={
                "JS_CONTEXT_SERIALIZER": "tests.static.test_checks._NotASerializer"
            }
        ):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W042"
        assert "JsContextSerializer protocol" in messages[0].msg

    def test_warns_when_instance_cannot_be_constructed(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": "tests.static.test_checks._Boom"}
        ):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W042"
        assert "cannot be instantiated" in messages[0].msg


class _Boom:
    """Class whose constructor raises, used by the W042 test."""

    def __init__(self) -> None:
        msg = "boom"
        raise TypeError(msg)


class TestAssetKindLoadableCheck:
    """check_asset_kinds_are_loadable flags kinds the runtime cannot insert."""

    def test_builtin_kinds_are_silent(self) -> None:
        assert check_asset_kinds_are_loadable() == []

    def test_module_renderer_kind_is_silent(self, monkeypatch) -> None:
        registry_with_vue = KindRegistry()
        registry_with_vue.register(
            "vue", extension=".vue", slot="scripts", renderer="render_module_tag"
        )
        monkeypatch.setattr(checks_module, "default_kinds", registry_with_vue)
        assert check_asset_kinds_are_loadable() == []

    def test_custom_renderer_kind_emits_w074(self, monkeypatch) -> None:
        mixed = KindRegistry()
        mixed.register(
            "css", extension=".css", slot="styles", renderer="render_link_tag"
        )
        mixed.register(
            "jsx", extension=".jsx", slot="scripts", renderer="render_babel_script_tag"
        )
        monkeypatch.setattr(checks_module, "default_kinds", mixed)
        messages = check_asset_kinds_are_loadable()
        assert _ids(messages) == ["next.W074"]
        assert isinstance(messages[0], DjangoWarning)
        assert "'jsx'" in messages[0].msg
        assert "'render_babel_script_tag'" in messages[0].msg


class TestReservedJsContextKeyCheck:
    """check_reserved_js_context_keys flags keys the init payload owns."""

    def test_no_keys_is_silent(self, monkeypatch) -> None:
        monkeypatch.setattr(
            checks_module, "iter_serialized_page_context_keys", lambda: iter(())
        )
        monkeypatch.setattr(
            checks_module, "iter_serialized_component_context_keys", lambda: iter(())
        )
        assert check_reserved_js_context_keys() == []

    def test_page_registering_a_reserved_key_emits_w075(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n\n"
            '@page.context("$csrf", serialize=True)\n'
            "def csrf_token():\n"
            '    return {"token": "app"}\n\n\n'
            '@page.context("unread", serialize=True)\n'
            "def unread():\n"
            "    return 3\n"
        )
        loaders_module._MODULE_MEMO.pop(page_file, None)
        with patch_checks_router_manager(
            pages_directory=tmp_path, scan_routes=[("test", page_file)]
        ):
            messages = check_reserved_js_context_keys()
        assert _ids(messages) == ["next.W075"]
        assert "'$csrf'" in messages[0].msg
        assert "Rename the key." in messages[0].msg

    def test_dev_key_message_names_the_debug_condition(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n\n"
            '@page.context("$dev", serialize=True)\n'
            "def dev_flag():\n"
            "    return False\n"
        )
        loaders_module._MODULE_MEMO.pop(page_file, None)
        with patch_checks_router_manager(
            pages_directory=tmp_path, scan_routes=[("test", page_file)]
        ):
            messages = check_reserved_js_context_keys()
        assert _ids(messages) == ["next.W075"]
        assert "'$dev'" in messages[0].msg
        assert "only while DEBUG is True" in messages[0].msg
        assert "never reaches window.Next.context" not in messages[0].msg

    def test_csrf_key_message_names_the_token_condition(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n\n"
            '@page.context("$csrf", serialize=True)\n'
            "def csrf_token():\n"
            '    return {"token": "app"}\n'
        )
        loaders_module._MODULE_MEMO.pop(page_file, None)
        with patch_checks_router_manager(
            pages_directory=tmp_path, scan_routes=[("test", page_file)]
        ):
            messages = check_reserved_js_context_keys()
        assert "can mint a CSRF token" in messages[0].msg

    def test_component_registering_a_reserved_key_emits_w075(self, tmp_path) -> None:
        comp_dir = tmp_path / "widget"
        comp_dir.mkdir()
        (comp_dir / "component.djx").write_text("<div/>")
        (comp_dir / "component.py").write_text(
            "from next.components import context\n\n\n"
            '@context("$csrf", serialize=True)\n'
            "def csrf_token():\n"
            '    return {"token": "app"}\n'
        )
        backend = FileComponentsBackend(
            {"DIRS": [str(tmp_path)], "COMPONENTS_DIR": "_components"}
        )
        manager = MagicMock()
        manager._backends = [backend]
        with patch(
            "next.components.context.get_components_manager", return_value=manager
        ):
            messages = check_reserved_js_context_keys()
        assert _ids(messages) == ["next.W075"]
        assert messages[0].msg.startswith("Component context key '$csrf'")
        assert messages[0].obj == str(comp_dir / "component.py")

    def test_unserialized_reserved_key_is_silent(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n\n"
            '@page.context("$csrf")\n'
            "def csrf_token():\n"
            '    return {"token": "app"}\n'
        )
        loaders_module._MODULE_MEMO.pop(page_file, None)
        with patch_checks_router_manager(
            pages_directory=tmp_path, scan_routes=[("test", page_file)]
        ):
            messages = check_reserved_js_context_keys()
        assert messages == []
