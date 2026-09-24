from __future__ import annotations

from typing import Any

import pytest
from django.test import override_settings
from pytest_lazy_fixtures import lf

from next.conf import next_framework_settings
from next.conf.frozen import FrozenDict, FrozenList
from next.conf.merge import (
    BOOL_KEYS,
    DICT_KEYS,
    LIST_KEYS,
    OPTIONAL_STR_KEYS,
    STR_KEYS,
    UNSET,
    accepted_value,
    merge_user_settings,
)
from next.conf.settings import NextFrameworkSettings


DEFAULTS: dict[str, Any] = NextFrameworkSettings.DEFAULTS

KEY_CLASSES = (LIST_KEYS, DICT_KEYS, STR_KEYS, OPTIONAL_STR_KEYS, BOOL_KEYS)


class TestKeyClasses:
    """Every declared default belongs to exactly one shape class."""

    @pytest.mark.parametrize("key", sorted(DEFAULTS))
    def test_each_default_key_has_one_shape(self, key: str) -> None:
        assert sum(key in shape for shape in KEY_CLASSES) == 1

    def test_no_shape_names_an_undeclared_key(self) -> None:
        named = set[str]().union(*KEY_CLASSES)
        assert named <= set(DEFAULTS)


class TestAcceptedValue:
    """`accepted_value` answers the merged value or `UNSET` per key shape."""

    @pytest.mark.parametrize(
        ("key", "raw"),
        [
            pytest.param("URL_RESOLVER", "myapp.Resolver", id="str"),
            pytest.param("PAGE_BACKENDS", [{"BACKEND": "myapp.Router"}], id="list"),
            pytest.param("NEXT_JS_OPTIONS", {"policy": "disabled"}, id="dict"),
            pytest.param("JS_CONTEXT_SERIALIZER", "myapp.dumps", id="optional_str"),
            pytest.param("JS_CONTEXT_SERIALIZER", None, id="optional_none"),
        ],
    )
    def test_usable_value_is_taken(self, key: str, raw: object) -> None:
        assert accepted_value(key, raw) == raw

    @pytest.mark.parametrize(
        ("key", "raw"),
        [
            pytest.param("URL_RESOLVER", 42, id="str"),
            pytest.param("PAGE_BACKENDS", "myapp.Router", id="list"),
            pytest.param("NEXT_JS_OPTIONS", [], id="dict"),
            pytest.param("JS_CONTEXT_SERIALIZER", 42, id="optional_str"),
        ],
    )
    def test_unusable_type_is_unset(self, key: str, raw: object) -> None:
        assert accepted_value(key, raw) is UNSET

    @pytest.mark.parametrize("raw", ["False", 0, [], None], ids=str)
    def test_bool_key_is_coerced_rather_than_refused(self, raw: object) -> None:
        assert accepted_value("STRICT_LOADING", raw) is bool(raw)

    def test_key_outside_every_shape_is_unset(self) -> None:
        """A third-party key added to `DEFAULTS` keeps the default it declared."""
        assert accepted_value("THIRD_PARTY_KEY", "value") is UNSET


@pytest.mark.parametrize(
    "settings_obj",
    [lf("fresh_next_framework_settings"), next_framework_settings],
    ids=["isolated", "global"],
)
class TestMergeUserSettings:
    """The merge lays usable user values over frozen defaults, one level deep.

    Both the isolated instance and the process-wide singleton walk this matrix, so
    a merged view one of the two keeps past a reload fails here.
    """

    @pytest.mark.parametrize("user", [None, {}], ids=["none", "empty"])
    def test_no_user_mapping_yields_the_defaults(
        self, settings_obj, user: dict[str, Any] | None
    ) -> None:
        merged = merge_user_settings(settings_obj.DEFAULTS, user)
        assert merged == DEFAULTS
        assert merged["PAGE_BACKENDS"] is not DEFAULTS["PAGE_BACKENDS"]

    def test_defaults_reach_the_caller_frozen(self, settings_obj) -> None:
        merged = merge_user_settings(settings_obj.DEFAULTS, None)
        assert isinstance(merged["PAGE_BACKENDS"], FrozenList)
        assert isinstance(merged["PAGE_BACKENDS"][0], FrozenDict)

    def test_a_key_outside_defaults_is_ignored(self, settings_obj) -> None:
        merged = merge_user_settings(settings_obj.DEFAULTS, {"MADE_UP": 1})
        assert "MADE_UP" not in merged

    def test_a_usable_value_replaces_the_default(self, settings_obj) -> None:
        with override_settings(NEXT_FRAMEWORK={"URL_NAME_TEMPLATE": "route_{name}"}):
            settings_obj.reload()
            assert settings_obj.URL_NAME_TEMPLATE == "route_{name}"

    def test_an_unusable_value_keeps_the_default(self, settings_obj) -> None:
        with override_settings(NEXT_FRAMEWORK={"URL_NAME_TEMPLATE": 42}):
            settings_obj.reload()
            assert DEFAULTS["URL_NAME_TEMPLATE"] == settings_obj.URL_NAME_TEMPLATE

    def test_a_reload_drops_the_merged_view_of_the_previous_settings(
        self, settings_obj
    ) -> None:
        """The merge runs once per reload, and both objects cache it their own way."""
        with override_settings(NEXT_FRAMEWORK={"URL_NAME_TEMPLATE": "route_{name}"}):
            settings_obj.reload()
            assert settings_obj.URL_NAME_TEMPLATE == "route_{name}"
        settings_obj.reload()
        assert DEFAULTS["URL_NAME_TEMPLATE"] == settings_obj.URL_NAME_TEMPLATE


class TestReplacementIsWhole:
    """A user value is never blended with the default value for its key."""

    @pytest.mark.parametrize(
        ("key", "user_value"),
        [
            pytest.param(
                "FORM_WIZARD_BACKEND", {"OPTIONS": {"TIMEOUT": 60}}, id="wizard_backend"
            ),
            pytest.param("NEXT_JS_OPTIONS", {"policy": "disabled"}, id="js_options"),
            pytest.param(
                "PAGE_BACKENDS", [{"BACKEND": "myapp.Router"}], id="page_backends"
            ),
        ],
    )
    def test_partial_override_drops_the_default_sub_keys(
        self, key: str, user_value: object
    ) -> None:
        merged = merge_user_settings(DEFAULTS, {key: user_value})
        assert merged[key] == user_value

    def test_wizard_backend_keeps_no_default_backend_path(self) -> None:
        """The one key whose merge used to fill `BACKEND` in now replaces whole."""
        merged = merge_user_settings(
            DEFAULTS, {"FORM_WIZARD_BACKEND": {"OPTIONS": {"TIMEOUT": 60}}}
        )
        assert "BACKEND" not in merged["FORM_WIZARD_BACKEND"]
