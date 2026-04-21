from __future__ import annotations

from typing import Any

import pytest
from django.test import override_settings
from pytest_lazy_fixtures import lf

from next.checks import (
    check_next_components_configuration,
    check_next_framework_unknown_top_level_keys,
    check_next_pages_configuration,
)
from next.conf import NextFrameworkSettings, next_framework_settings, perform_import
from next.urls import RouterBackend, RouterFactory


class TestLazyFixtureWiring:
    """Lazy fixture references resolve shared fixtures from tests.fixtures."""

    @pytest.mark.parametrize("settings_obj", [lf("fresh_next_framework_settings")])
    def test_parametrize_uses_lazy_fixture_reference(self, settings_obj) -> None:
        """Parametrize can reference fixtures by name without extra imports."""
        assert isinstance(settings_obj, NextFrameworkSettings)


class TestNextFrameworkSettingsDjangoIntegration:
    """Global next_framework_settings with override_settings(NEXT_FRAMEWORK=...)."""

    @pytest.mark.parametrize(
        "next_framework",
        [
            {},
            42,
        ],
        ids=["empty_dict", "non_dict"],
    )
    def test_falls_back_to_default_page_router_backend(
        self,
        next_framework: object,
    ) -> None:
        """Empty or invalid NEXT_FRAMEWORK keeps the default file router backend."""
        with override_settings(NEXT_FRAMEWORK=next_framework):  # type: ignore[arg-type]
            next_framework_settings.reload()
            assert next_framework_settings.DEFAULT_PAGE_BACKENDS[0]["BACKEND"] == (
                "next.urls.FileRouterBackend"
            )

    def test_default_page_routers_replaces_default_list_entirely(self) -> None:
        """Providing DEFAULT_PAGE_BACKENDS replaces the default list."""
        custom = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "PAGES_DIR": "custom_pages",
                "APP_DIRS": False,
                "DIRS": [],
                "OPTIONS": {},
            },
        ]
        with override_settings(NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": custom}):
            next_framework_settings.reload()
            assert custom == next_framework_settings.DEFAULT_PAGE_BACKENDS
            assert (
                next_framework_settings.DEFAULT_PAGE_BACKENDS[0]["PAGES_DIR"]
                == "custom_pages"
            )

    def test_components_only_merge_leaves_routers_default(self) -> None:
        """Overriding only DEFAULT_COMPONENT_BACKENDS leaves page routers as defaults."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": ["/tmp/x"],
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            assert (
                next_framework_settings.DEFAULT_PAGE_BACKENDS[0]["PAGES_DIR"] == "pages"
            )
            assert next_framework_settings.DEFAULT_COMPONENT_BACKENDS[0]["DIRS"] == [
                "/tmp/x",
            ]

    @pytest.mark.parametrize(
        "bad_attr",
        ["NOT_A_KEY", "FOO", "PAGES", ""],
        ids=["unknown_upper", "unknown_short", "old_key_name", "empty_string"],
    )
    def test_unknown_attribute_raises(self, bad_attr: str) -> None:
        """Only top level keys from DEFAULTS are valid attributes."""
        with pytest.raises(AttributeError, match="Invalid Next framework setting"):
            getattr(next_framework_settings, bad_attr)

    def test_only_url_name_template_overridden(self) -> None:
        """Setting only URL_NAME_TEMPLATE keeps default page backends."""
        with override_settings(
            NEXT_FRAMEWORK={"URL_NAME_TEMPLATE": "view_{name}"},
        ):
            next_framework_settings.reload()
            assert next_framework_settings.URL_NAME_TEMPLATE == "view_{name}"
            assert (
                NextFrameworkSettings.DEFAULTS["DEFAULT_PAGE_BACKENDS"]
                == next_framework_settings.DEFAULT_PAGE_BACKENDS
            )

    @pytest.mark.parametrize(
        ("next_framework", "setting_name"),
        [
            ({"DEFAULT_PAGE_BACKENDS": "invalid"}, "DEFAULT_PAGE_BACKENDS"),
            ({"URL_NAME_TEMPLATE": 123}, "URL_NAME_TEMPLATE"),
        ],
        ids=["invalid_routers_type", "invalid_url_name_template"],
    )
    def test_invalid_setting_type_keeps_default(
        self,
        next_framework: dict[str, Any],
        setting_name: str,
    ) -> None:
        """Wrong type for a key is ignored and the default value is kept."""
        with override_settings(NEXT_FRAMEWORK=next_framework):  # type: ignore[arg-type, dict-item]
            next_framework_settings.reload()
            assert NextFrameworkSettings.DEFAULTS[setting_name] == getattr(
                next_framework_settings, setting_name
            )


class TestNextFrameworkSettingsFlatMerge:
    """Unit tests for NextFrameworkSettings._build_flat_merged."""

    @pytest.mark.parametrize(
        ("user", "expected_routers_len"),
        [
            (None, 1),
            ({}, 1),
            ({"DEFAULT_PAGE_BACKENDS": []}, 0),
        ],
        ids=["none_user", "empty_user_dict", "explicit_empty_routers"],
    )
    def test_build_flat_merged_routers_length(
        self,
        fresh_next_framework_settings: NextFrameworkSettings,
        user: dict[str, Any] | None,
        expected_routers_len: int,
    ) -> None:
        """DEFAULT_PAGE_BACKENDS length follows the merged user value."""
        merged = fresh_next_framework_settings._build_flat_merged(user)
        assert len(merged["DEFAULT_PAGE_BACKENDS"]) == expected_routers_len

    def test_build_flat_merge_empty_component_backends(
        self,
        fresh_next_framework_settings: NextFrameworkSettings,
    ) -> None:
        """Explicit empty DEFAULT_COMPONENT_BACKENDS is preserved."""
        user = {"DEFAULT_COMPONENT_BACKENDS": []}
        merged = fresh_next_framework_settings._build_flat_merged(user)
        assert merged["DEFAULT_COMPONENT_BACKENDS"] == []


class TestFlatNextFrameworkBehavior:
    """Per key overrides without nested PAGES or COMPONENTS namespaces."""

    def test_cannot_assign_framework_keys(self) -> None:
        """Top level keys must not be assigned on the settings object."""
        with pytest.raises(AttributeError, match="cannot be assigned"):
            next_framework_settings.URL_NAME_TEMPLATE = "x"  # type: ignore[misc]

    def test_perform_import_raises_import_error(self) -> None:
        """Invalid dotted path raises ImportError with context."""
        with pytest.raises(ImportError, match="no_such_module_zzz"):
            perform_import("no_such_module_zzz.ClassName", "TEST_SETTING")

    def test_perform_import_returns_non_string_unchanged(self) -> None:
        """Non string values are returned as is for future IMPORT_STRINGS use."""
        assert perform_import(42, "X") == 42

    def test_setattr_allows_internal_attributes(self) -> None:
        """Attributes outside DEFAULTS keys may be set for tests or hooks."""
        try:
            next_framework_settings._coverage_probe = 1
            assert next_framework_settings._coverage_probe == 1
        finally:
            del next_framework_settings._coverage_probe


class TestNextFrameworkChecksUnknownKeys:
    """System checks reject keys that are not part of the supported schema."""

    def test_next_framework_top_level_unknown_key(self) -> None:
        """NEXT_FRAMEWORK must not contain keys outside framework defaults."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": True,
                        "DIRS": [],
                        "OPTIONS": {},
                    },
                ],
                "NOT_A_FRAMEWORK_KEY": True,
            },
        ):
            next_framework_settings.reload()
            errors = check_next_framework_unknown_top_level_keys()
        assert any(e.id == "next.E035" for e in errors)

    def test_file_router_entry_unknown_key(self) -> None:
        """FileRouterBackend dicts only allow documented top-level keys."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": True,
                        "DIRS": [],
                        "OPTIONS": {},
                        "made_up_option": True,
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            errors = check_next_pages_configuration()
        assert any(e.id == "next.E035" for e in errors)

    def test_file_router_rejects_components_dir_key(self) -> None:
        """``COMPONENTS_DIR`` belongs on component backends, not page router dicts."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": True,
                        "DIRS": [],
                        "OPTIONS": {},
                        "COMPONENTS_DIR": "_components",
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            errors = check_next_pages_configuration()
        assert any(e.id == "next.E035" for e in errors)

    def test_component_backend_unknown_key(self) -> None:
        """Component backend dicts only allow BACKEND, DIRS, and COMPONENTS_DIR."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                        "extra_key": 1,
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            errors = check_next_components_configuration()
        assert any(e.id == "next.E035" for e in errors)

    def test_non_file_router_entry_only_backend_key(self) -> None:
        """Non-file router entries must not carry extra configuration keys."""
        backend_path = "conf_test_checks.MinimalRouter"

        class MinimalRouter(RouterBackend):
            def generate_urls(self) -> list:
                return []

        RouterFactory.register_backend(backend_path, MinimalRouter)
        try:
            with override_settings(
                NEXT_FRAMEWORK={
                    "DEFAULT_PAGE_BACKENDS": [
                        {
                            "BACKEND": backend_path,
                            "PAGES_DIR": "pages",
                        },
                    ],
                },
            ):
                next_framework_settings.reload()
                errors = check_next_pages_configuration()
        finally:
            RouterFactory._backends.pop(backend_path, None)
        assert any(e.id == "next.E035" for e in errors)
