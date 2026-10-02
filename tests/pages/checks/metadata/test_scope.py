import pytest
from django.test import override_settings

from next.pages.checks import check_metadata_settings_scope
from next.pages.metadata import RESET, Replace
from next.pages.metadata.scope import METADATA_KEYS, SITE_SOURCE
from tests.pages.checks.metadata.trees import DESCRIPTION, scope
from tests.support import check_ids


class TestSettingsScope:
    """`check_metadata_settings_scope` reads the raw `METADATA` scope."""

    @pytest.mark.parametrize(
        "framework",
        [
            scope(
                DEFAULTS={
                    "site_name": "Acme",
                    "title": {"template": "{title} · Acme", "default": "Acme"},
                    "description": DESCRIPTION,
                }
            ),
            {},
            {"METADATA": "x"},
            scope(CANONICAL_QUERY=["page"], RENDERER="next.pages.HtmlMetadataRenderer"),
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
        ("dotted", "fragment"),
        [
            ("nope.Renderer", "could not be imported"),
            ("next.pages.Metadata", "is not a next.pages.MetadataRenderer subclass"),
            ("next.pages.MetadataRenderer", "is abstract"),
        ],
        ids=["import", "family", "abstract"],
    )
    def test_a_renderer_that_cannot_render_is_e107(
        self, dotted: str, fragment: str
    ) -> None:
        with override_settings(NEXT_FRAMEWORK=scope(RENDERER=dotted)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.E107"]
        assert fragment in messages[0].msg
        assert "falls back to HtmlMetadataRenderer" in messages[0].msg

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
        "defaults",
        [{"description": RESET}, {"og": {"images": Replace(["/a.png"])}}],
        ids=["reset", "nested_replace"],
    )
    def test_a_replace_in_the_defaults_is_w109(
        self, defaults: dict[str, object]
    ) -> None:
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.W109"]

    def test_two_jsonld_objects_with_one_id_is_w108(self) -> None:
        defaults = {"jsonld": [{"@id": "#org"}, {"@id": "#org"}, {"@id": "#site"}]}
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.W108"]
        assert "'/#org'" in messages[0].msg
        assert "'#site'" not in messages[0].msg

    def test_an_empty_default_title_is_e105(self) -> None:
        defaults = {"title": {"default": ""}}
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.E105"]

    def test_every_settings_finding_is_reported_together(self) -> None:
        defaults = {"title": {"template": "{title}"}}
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults, NOPE=1)):
            messages = check_metadata_settings_scope()
        assert check_ids(messages) == ["next.E035", "next.E100"]
