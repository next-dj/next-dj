from pathlib import Path

import pytest
from django.test import override_settings

from next.pages.checks import check_metadata_title_templates
from next.pages.metadata import SITE_SOURCE
from tests.pages.checks.metadata.trees import I18N, metadata_page, scope, templated_page
from tests.support import check_ids, patch_checks_router_manager


PER_LANGUAGE_TEMPLATE = """
from django.utils.functional import lazy
from django.utils.translation import get_language


def _text():
    if (get_language() or "").startswith("de"):
        return "{nope} · Seite"
    return "{title} · Site"


metadata = {"title": {"template": lazy(_text, str)(), "default": "Site"}}
"""


class TestTitleTemplates:
    """`check_metadata_title_templates` parses both tiers under every language."""

    def test_valid_templates_are_silent(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"title": {"template": "{title} · {site_name}"}}')
        defaults = {"title": {"template": "{title} · Acme", "default": "Acme"}}
        with (
            override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_metadata_title_templates() == []

    @pytest.mark.parametrize(
        ("template", "fragment"),
        [
            ("{nope} · Acme", "names the placeholder 'nope'"),
            ("{title:>10}", "formats the placeholder 'title'"),
            ("{title!r}", "formats the placeholder 'title'"),
            ("{title.upper}", "names the placeholder 'title.upper'"),
            ("{title[0]}", "names the placeholder 'title[0]'"),
            ("{title", "is malformed"),
        ],
        ids=["unknown", "spec", "conversion", "attribute", "index", "unbalanced"],
    )
    def test_a_settings_template_the_parser_refuses_is_e099(
        self, tmp_path: Path, template: str, fragment: str
    ) -> None:
        defaults = {"title": {"template": template, "default": "Acme"}}
        with (
            override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert check_ids(messages) == ["next.E099"]
        assert fragment in messages[0].msg
        assert SITE_SOURCE in messages[0].msg
        assert "under the language" not in messages[0].msg

    def test_a_page_template_the_parser_refuses_is_e099(self, tmp_path: Path) -> None:
        page_file = metadata_page(
            tmp_path, '{"title": {"template": "{nope}", "default": "X"}}'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_title_templates()
        assert check_ids(messages) == ["next.E099"]
        assert str(page_file) in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_a_template_broken_in_one_language_names_that_language(
        self, tmp_path: Path
    ) -> None:
        templated_page(tmp_path, PER_LANGUAGE_TEMPLATE)
        with (
            override_settings(**I18N),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert check_ids(messages) == ["next.E099"]
        assert "under the language 'de'" in messages[0].msg
        assert "'{nope} · Seite'" in messages[0].msg
        assert "'en'" not in messages[0].msg

    def test_a_template_valid_in_the_only_language_is_silent(
        self, tmp_path: Path
    ) -> None:
        templated_page(tmp_path, PER_LANGUAGE_TEMPLATE)
        with (
            override_settings(USE_I18N=False),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_metadata_title_templates() == []

    def test_one_finding_under_every_language_is_reported_once(
        self, tmp_path: Path
    ) -> None:
        defaults = {"title": {"template": "{nope}", "default": "Acme"}}
        with (
            override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults), **I18N),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert check_ids(messages) == ["next.E099"]
        assert "under the language" not in messages[0].msg

    def test_a_finding_under_some_languages_names_them(self, tmp_path: Path) -> None:
        templated_page(tmp_path, PER_LANGUAGE_TEMPLATE)
        languages = [("en", "English"), ("de", "German"), ("de-at", "Austrian")]
        with (
            override_settings(**{**I18N, "LANGUAGES": languages}),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert check_ids(messages) == ["next.E099"]
        assert "under the languages 'de', 'de-at'" in messages[0].msg

    def test_a_settings_template_without_title_is_w084(self, tmp_path: Path) -> None:
        defaults = {"title": {"template": "Acme", "default": "Acme"}}
        with (
            override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert check_ids(messages) == ["next.W084"]
        assert messages[0].hint == (
            "Write {title}, the %s placeholder of Next.js is not substituted here."
        )

    def test_a_page_template_without_title_is_w084(self, tmp_path: Path) -> None:
        page_file = metadata_page(
            tmp_path, '{"title": {"template": "%s · Site", "default": "Site"}}'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_title_templates()
        assert check_ids(messages) == ["next.W084"]
        assert messages[0].obj == str(page_file)

    def test_a_refused_defaults_tier_contributes_no_template(
        self, tmp_path: Path
    ) -> None:
        with (
            override_settings(NEXT_FRAMEWORK=scope(DEFAULTS={"title": "Acme"})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_metadata_title_templates() == []
