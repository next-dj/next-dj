from __future__ import annotations

import pytest
from django.conf import settings
from django.core.checks import run_checks
from django.test import override_settings

from next.checks import NEXT, check_next_framework_value_types, register_all
from next.conf.checks import _KEY_TYPES
from next.conf.settings import NextFrameworkSettings
from next.forms.checks import (
    check_form_action_backends_configuration,
    check_form_anchor_files,
    check_form_wizard_backend,
)
from next.static.checks import check_js_context_serializer


VALID_TYPED_VALUES: dict[str, object] = {
    "PAGE_BACKENDS": [],
    "COMPONENT_BACKENDS": [],
    "STATIC_BACKENDS": [],
    "PARTIAL_BACKENDS": [],
    "TEMPLATE_LOADERS": [],
    "URL_NAME_TEMPLATE": "page_{name}",
    "URL_RESOLVER": "next.urls.TrieURLResolver",
    "NEXT_JS_OPTIONS": {},
}


class TestValueTypeErrors:
    """`check_next_framework_value_types` reports mistyped keys as next.E076."""

    def test_key_types_map_covers_exactly_eight_keys(self) -> None:
        assert set(_KEY_TYPES) == set(VALID_TYPED_VALUES)

    @pytest.mark.parametrize(
        ("key", "bad_value"),
        [
            ("PAGE_BACKENDS", "x"),
            ("COMPONENT_BACKENDS", {}),
            ("STATIC_BACKENDS", {}),
            ("PARTIAL_BACKENDS", {}),
            ("TEMPLATE_LOADERS", {}),
            ("URL_NAME_TEMPLATE", []),
            ("URL_RESOLVER", []),
            ("NEXT_JS_OPTIONS", []),
        ],
    )
    def test_mistyped_key_yields_error(self, key: str, bad_value: object) -> None:
        with override_settings(NEXT_FRAMEWORK={key: bad_value}):
            messages = check_next_framework_value_types()
        assert [m.id for m in messages] == ["next.E076"]
        assert f"NEXT_FRAMEWORK[{key!r}]" in messages[0].msg

    def test_silent_on_valid_map_of_all_eight_keys(self) -> None:
        with override_settings(NEXT_FRAMEWORK=dict(VALID_TYPED_VALUES)):
            assert check_next_framework_value_types() == []

    def test_silent_when_setting_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(settings, "NEXT_FRAMEWORK")
        assert check_next_framework_value_types() == []

    @pytest.mark.parametrize(
        "next_framework",
        [{}, {"STRICT_CONTEXT": True}],
        ids=["empty_dict", "typed_key_absent"],
    )
    def test_silent_without_typed_keys(self, next_framework: object) -> None:
        with override_settings(NEXT_FRAMEWORK=next_framework):  # type: ignore[arg-type]
            assert check_next_framework_value_types() == []

    def test_non_dict_setting_yields_error(self) -> None:
        with override_settings(NEXT_FRAMEWORK=42):  # type: ignore[arg-type]
            (message,) = check_next_framework_value_types()
        assert message.id == "next.E076"
        assert message.msg == (
            "NEXT_FRAMEWORK must be a dict, got 'int'. The settings layer "
            "ignores a non-dict value entirely and uses the framework "
            "defaults."
        )
        assert message.hint == (
            "Fix the value in settings.NEXT_FRAMEWORK, or silence this check "
            "by adding its id to SILENCED_SYSTEM_CHECKS."
        )

    def test_registered_check_reaches_manage_py_check_path(self) -> None:
        register_all()
        with override_settings(NEXT_FRAMEWORK={"NEXT_JS_OPTIONS": []}):
            messages = run_checks(tags=[NEXT])
        assert any(m.id == "next.E076" for m in messages)


class TestBoolCoercionWarnings:
    """`check_next_framework_value_types` flags non-bool flags as next.W072."""

    @pytest.mark.parametrize(
        "key",
        sorted(NextFrameworkSettings._BOOL_KEYS),
    )
    def test_non_bool_flag_yields_warning(self, key: str) -> None:
        with override_settings(NEXT_FRAMEWORK={key: "False"}):
            messages = check_next_framework_value_types()
        assert [m.id for m in messages] == ["next.W072"]
        assert f"NEXT_FRAMEWORK[{key!r}]" in messages[0].msg

    @pytest.mark.parametrize("key", sorted(NextFrameworkSettings._BOOL_KEYS))
    @pytest.mark.parametrize("value", [True, False], ids=["true", "false"])
    def test_silent_on_strict_bool(self, key: str, value: object) -> None:
        with override_settings(NEXT_FRAMEWORK={key: value}):
            assert check_next_framework_value_types() == []


class TestKeysWithDedicatedRawChecks:
    """Keys with their own raw checks stay outside the type map and bool branch."""

    DEDICATED_KEYS = frozenset(
        {
            "FORM_WIZARD_BACKEND",
            "FORM_ANCHOR_FILES",
            "FORM_ACTION_BACKENDS",
            "JS_CONTEXT_SERIALIZER",
        }
    )

    def test_dedicated_keys_excluded_from_both_branches(self) -> None:
        covered = set(_KEY_TYPES) | NextFrameworkSettings._BOOL_KEYS
        assert not self.DEDICATED_KEYS & covered

    def test_every_default_key_has_exactly_one_owner(self) -> None:
        owners = (
            set(_KEY_TYPES),
            set(NextFrameworkSettings._BOOL_KEYS),
            set(self.DEDICATED_KEYS),
        )
        for key in NextFrameworkSettings.DEFAULTS:
            assert sum(key in owner for owner in owners) == 1, key

    def test_dedicated_checks_still_fire_without_duplicates(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "FORM_WIZARD_BACKEND": "oops",
                "FORM_ANCHOR_FILES": 42,
                "FORM_ACTION_BACKENDS": "oops",
                "JS_CONTEXT_SERIALIZER": 42,
            }
        ):
            assert check_next_framework_value_types() == []
            assert [m.id for m in check_form_wizard_backend()] == ["next.E051"]
            assert [m.id for m in check_form_anchor_files()] == ["next.E052"]
            assert [m.id for m in check_form_action_backends_configuration()] == [
                "next.E044"
            ]
            assert [m.id for m in check_js_context_serializer()] == ["next.W042"]


class TestSilencing:
    """SILENCED_SYSTEM_CHECKS mutes each new code independently."""

    def test_silences_error_code_only(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": "x", "STRICT_CONTEXT": "False"},
            SILENCED_SYSTEM_CHECKS=["next.E076"],
        ):
            messages = check_next_framework_value_types()
            silenced = {m.id: m.is_silenced() for m in messages}
        assert silenced == {"next.E076": True, "next.W072": False}

    def test_silences_warning_code_only(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": "x", "STRICT_CONTEXT": "False"},
            SILENCED_SYSTEM_CHECKS=["next.W072"],
        ):
            messages = check_next_framework_value_types()
            silenced = {m.id: m.is_silenced() for m in messages}
        assert silenced == {"next.E076": False, "next.W072": True}


class TestMessageWording:
    """Golden message texts stay stable for the documentation phase."""

    def test_error_message_text(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": "x"}):
            (message,) = check_next_framework_value_types()
        assert message.msg == (
            "NEXT_FRAMEWORK['PAGE_BACKENDS'] must be a list, got 'str'. "
            "The settings merge ignores this value and silently keeps the "
            "framework default."
        )
        assert message.hint == (
            "Fix the value in settings.NEXT_FRAMEWORK, or silence this check "
            "by adding its id to SILENCED_SYSTEM_CHECKS."
        )

    def test_warning_message_text(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"STRICT_CONTEXT": "False"}):
            (message,) = check_next_framework_value_types()
        assert message.msg == (
            "NEXT_FRAMEWORK['STRICT_CONTEXT'] should be a bool, got 'str'. "
            "The bool() coercion turns falsy-looking strings such as 'False' "
            "into True."
        )
        assert message.hint == (
            "Fix the value in settings.NEXT_FRAMEWORK, or silence this check "
            "by adding its id to SILENCED_SYSTEM_CHECKS."
        )
