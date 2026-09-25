import pytest
from django.test import override_settings

from next.pages.checks import check_metadata_settings_scope
from next.pages.metadata import SITE_SOURCE
from next.pages.metadata.scope import METADATA_KEYS
from tests.pages.checks.metadata.trees import BASE, DESCRIPTION, scope
from tests.support import check_ids


class TestSettingsScope:
    """`check_metadata_settings_scope` reads the raw `METADATA` scope."""

    @pytest.mark.parametrize(
        "framework",
        [
            scope(
                DEFAULTS={
                    "base": BASE,
                    "title": {"template": "{title} · Acme", "default": "Acme"},
                    "description": DESCRIPTION,
                }
            ),
            {},
            {"METADATA": "x"},
            scope(
                NOINDEX=True,
                CANONICAL_QUERY=["page"],
                CHECKS={},
                RENDERER="next.pages.HtmlMetadataRenderer",
            ),
        ],
        ids=["valid_defaults", "absent", "non_dict_left_to_conf", "every_option"],
    )
    def test_a_scope_with_nothing_to_report_is_silent(
        self, framework: dict[str, object]
    ) -> None:
        with override_settings(NEXT_FRAMEWORK=framework):
            assert check_metadata_settings_scope() == []

    def test_an_unknown_option_is_e035(self) -> None:
        with override_settings(NEXT_FRAMEWORK=scope(BOGUS=1)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.E035"]
        assert "NEXT_FRAMEWORK['METADATA']" in messages[0].msg
        assert "'BOGUS'" in messages[0].msg
        assert ", ".join(sorted(METADATA_KEYS)) in messages[0].msg

    @pytest.mark.parametrize(
        ("defaults", "fragment"),
        [
            ("x", "must be a mapping"),
            ({"title": "Acme"}, "declares metadata key 'title' as 'str'"),
            ({"title": {"absolute": "Acme"}}, "'title.absolute'"),
            ({"foo": 1}, "declares metadata key 'foo'"),
            ({"og": {"images": [1]}}, "'og.images[0]'"),
        ],
        ids=["non_dict", "bare_title", "absolute", "unknown_key", "nested_type"],
    )
    def test_a_refused_defaults_tier_is_e098(self, defaults, fragment) -> None:
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.E098"]
        assert fragment in messages[0].msg
        assert SITE_SOURCE in messages[0].msg

    def test_a_template_without_default_is_e100(self) -> None:
        defaults = {"title": {"template": "{title} · Acme"}}
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.E100"]
        assert SITE_SOURCE in messages[0].msg

    @pytest.mark.parametrize(
        "base",
        [
            "acme.example",
            "ftp://acme.example",
            "https://acme.example/blog",
            "https://acme.example/?x=1",
            "https://acme.example/#top",
            "https://",
        ],
        ids=["no_scheme", "ftp", "path", "query", "fragment", "empty_host"],
    )
    def test_a_base_that_is_no_origin_is_e101(self, base: str) -> None:
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS={"base": base})):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.E101"]
        assert repr(base) in messages[0].msg

    @pytest.mark.parametrize(
        "base",
        [BASE, f"{BASE}/", "http://localhost:8000"],
        ids=["bare", "trailing_slash", "localhost_port"],
    )
    def test_an_origin_base_is_silent(self, base: str) -> None:
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS={"base": base})):
            assert check_metadata_settings_scope() == []

    def test_an_empty_default_title_is_e105(self) -> None:
        defaults = {"title": {"default": ""}}
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.E105"]

    def test_every_settings_finding_is_reported_together(self) -> None:
        defaults = {"title": {"template": "{title}"}, "base": "x"}
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults, NOPE=1)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.E035", "next.E100", "next.E101"]
