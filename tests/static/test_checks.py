from __future__ import annotations

from django.core.checks import Error, Warning as DjangoWarning
from django.test import override_settings

from next.static.checks import check_static_backends


def _ids(messages: list) -> list[str]:
    return [m.id for m in messages]


class TestEmptyConfig:
    def test_empty_list_emits_w030(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"DEFAULT_STATIC_BACKENDS": []}):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.W030"]
        assert isinstance(messages[0], DjangoWarning)

    def test_non_list_falls_back_to_defaults(self) -> None:
        """Conf coerces non-list to defaults, so checks treat it as valid."""
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_STATIC_BACKENDS": "not-a-list"}
        ):
            messages = check_static_backends(app_configs=None)
        assert messages == []


class TestValidConfig:
    def test_single_default_backend(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_STATIC_BACKENDS": [
                    {"BACKEND": "next.static.StaticFilesBackend"}
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert messages == []

    def test_valid_options_with_placeholders(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_STATIC_BACKENDS": [
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
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_STATIC_BACKENDS": ["not-a-dict"]}
        ):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.E037"]
        assert isinstance(messages[0], Error)

    def test_non_string_backend_emits_e037(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_STATIC_BACKENDS": [{"BACKEND": 123}]}
        ):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.E037"]

    def test_missing_module_emits_e036(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_STATIC_BACKENDS": [{"BACKEND": "does.not.exist.Backend"}]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.E036"]

    def test_not_subclass_emits_e037(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_STATIC_BACKENDS": [{"BACKEND": "builtins.dict"}]}
        ):
            messages = check_static_backends(app_configs=None)
        assert _ids(messages) == ["next.E037"]

    def test_duplicate_backend_emits_e038(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_STATIC_BACKENDS": [
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
                "DEFAULT_STATIC_BACKENDS": [
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
                "DEFAULT_STATIC_BACKENDS": [
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
                "DEFAULT_STATIC_BACKENDS": [
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
        from django.core.checks.registry import registry

        ids = {getattr(c, "__name__", None) for c in registry.registered_checks}
        assert "check_static_backends" in ids
