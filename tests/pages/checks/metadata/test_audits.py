import re
from pathlib import Path

import pytest
from django.core.checks import Tags
from django.core.checks.registry import registry as check_registry
from django.test import override_settings

from next.checks import NEXT, SEO, register_all
from next.pages.checks import (
    check_seo_alternates,
    check_seo_canonical,
    check_seo_description,
    check_seo_titles,
)
from tests.pages.checks.metadata.trees import (
    BASE,
    DESCRIPTION,
    I18N,
    INHERITED_CALLABLE,
    framework,
    metadata_page,
    scope,
    templated_page,
)
from tests.support import check_ids, patch_checks_router_manager


OWN_CALLABLE = """
from next.pages import page


@page.metadata
def meta() -> dict:
    return {"title": "Dynamic"}
"""


class TestSeoDescription:
    """`check_seo_description` audits presence and length."""

    def test_a_missing_description_is_w089(self, tmp_path: Path) -> None:
        page_file = metadata_page(tmp_path, '{"title": "Home"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_description()
        assert check_ids(messages) == ["next.W089"]
        assert messages[0].obj == str(page_file)

    def test_a_missing_description_is_silent_when_not_required(
        self, tmp_path: Path
    ) -> None:
        metadata_page(tmp_path, '{"title": "Home"}')
        checks = {"REQUIRE_DESCRIPTION": False}
        with (
            override_settings(NEXT_FRAMEWORK=scope(CHECKS=checks)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_seo_description() == []

    def test_a_description_from_the_settings_tier_is_silent(
        self, tmp_path: Path
    ) -> None:
        metadata_page(tmp_path, '{"title": "Home"}')
        defaults = {"description": DESCRIPTION}
        with (
            override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_seo_description() == []

    @pytest.mark.parametrize(
        "description", ["Too short.", "x" * 161], ids=["short", "long"]
    )
    def test_a_description_outside_the_window_is_w092(
        self, tmp_path: Path, description: str
    ) -> None:
        metadata_page(tmp_path, f'{{"description": "{description}"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_description()
        assert check_ids(messages) == ["next.W092"]
        assert f"{len(description)} characters" in messages[0].msg

    def test_a_raised_maximum_widens_the_window(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, f'{{"description": "{"x" * 161}"}}')
        with (
            override_settings(NEXT_FRAMEWORK=scope(CHECKS={"DESCRIPTION_MAX": 200})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_seo_description() == []

    def test_an_unusable_threshold_keeps_the_default(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, f'{{"description": "{"x" * 161}"}}')
        with (
            override_settings(NEXT_FRAMEWORK=scope(CHECKS={"DESCRIPTION_MAX": True})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_ids(check_seo_description()) == ["next.W092"]


class TestSeoTitles:
    """`check_seo_titles` audits duplicates and length."""

    def test_two_pages_with_one_title_are_one_w090(self, tmp_path: Path) -> None:
        first = metadata_page(tmp_path / "a", '{"title": "Same"}')
        metadata_page(tmp_path / "b", '{"title": "Same"}')
        metadata_page(tmp_path / "c", '{"title": "Other"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_titles()
        assert check_ids(messages) == ["next.W090"]
        assert "'/a'" in messages[0].msg
        assert "'/b'" in messages[0].msg
        assert "'/c'" not in messages[0].msg
        assert messages[0].obj == str(first)

    def test_a_templated_duplicate_is_compared_after_the_fold(
        self, tmp_path: Path
    ) -> None:
        metadata_page(
            tmp_path, '{"title": {"template": "{title} · X", "default": "X"}}'
        )
        metadata_page(tmp_path / "a", '{"title": "Same"}')
        metadata_page(tmp_path / "b", '{"title": {"absolute": "Same · X"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_titles()
        assert check_ids(messages) == ["next.W090"]
        assert "'Same · X'" in messages[0].msg

    def test_distinct_titles_are_silent(self, tmp_path: Path) -> None:
        metadata_page(tmp_path / "a", '{"title": "One"}')
        metadata_page(tmp_path / "b", '{"title": "Two"}')
        templated_page(tmp_path / "c", "x = 1\n")
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_seo_titles() == []

    def test_a_long_title_is_w091(self, tmp_path: Path) -> None:
        page_file = metadata_page(tmp_path, f'{{"title": "{"t" * 61}"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_titles()
        assert check_ids(messages) == ["next.W091"]
        assert "61 characters, over 60" in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_a_raised_maximum_allows_the_title(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, f'{{"title": "{"t" * 61}"}}')
        with (
            override_settings(NEXT_FRAMEWORK=scope(CHECKS={"TITLE_MAX": 70})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_seo_titles() == []


class TestSeoAuditsSkipDynamicPages:
    """The title and description audits leave a page a callable rewrites alone."""

    def _tree(self, tmp_path: Path) -> Path:
        metadata_page(tmp_path, '{"title": "Site"}')
        return metadata_page(tmp_path / "static", '{"title": "Site"}')

    def test_a_page_with_its_own_callable_is_skipped(self, tmp_path: Path) -> None:
        sibling = self._tree(tmp_path)
        templated_page(tmp_path / "dynamic", OWN_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            titles = check_seo_titles()
            descriptions = check_seo_description()
        assert check_ids(titles) == ["next.W090"]
        assert "'/dynamic'" not in titles[0].msg
        assert sorted(m.obj for m in descriptions) == sorted(
            [str(tmp_path / "page.py"), str(sibling)]
        )

    def test_a_page_under_an_inherited_callable_is_skipped(
        self, tmp_path: Path
    ) -> None:
        self._tree(tmp_path)
        templated_page(tmp_path / "posts", INHERITED_CALLABLE)
        metadata_page(tmp_path / "posts" / "one", '{"title": "Site"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            titles = check_seo_titles()
            descriptions = check_seo_description()
        assert check_ids(titles) == ["next.W090"]
        assert "'/posts" not in titles[0].msg
        assert "'/'" in titles[0].msg
        assert "'/static'" in titles[0].msg
        assert all("posts" not in str(m.obj) for m in descriptions)

    def test_a_page_beside_an_uninherited_callable_is_still_audited(
        self, tmp_path: Path
    ) -> None:
        self._tree(tmp_path)
        templated_page(tmp_path / "posts", OWN_CALLABLE)
        metadata_page(tmp_path / "posts" / "one", '{"title": "Site"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            titles = check_seo_titles()
        assert check_ids(titles) == ["next.W090"]
        trails = re.findall(r"'(/[^']*)'", titles[0].msg.split(" fold to ")[0])
        assert sorted(trails) == ["/", "/posts/one", "/static"]
        assert titles[0].obj == str(tmp_path / trails[0].strip("/") / "page.py")

    def test_the_canonical_and_alternates_audits_still_read_a_dynamic_page(
        self, tmp_path: Path
    ) -> None:
        templated_page(
            tmp_path / "dynamic",
            'metadata = {"canonical": "/nowhere/", '
            '"alternates": {"languages": {"en": "/en/"}}}\n',
        )
        templated_page(tmp_path / "dynamic" / "leaf", OWN_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            canonical = check_seo_canonical()
            alternates = check_seo_alternates()
        assert check_ids(canonical) == ["next.W093", "next.W093"]
        assert check_ids(alternates) == ["next.W095", "next.W095"]


class TestSeoCanonical:
    """`check_seo_canonical` audits literal canonicals."""

    @pytest.mark.parametrize(
        "metadata",
        [
            '{"canonical": "/nowhere/"}',
            f'{{"base": "{BASE}", "canonical": "{BASE}/nowhere/"}}',
        ],
        ids=["relative", "absolute_on_base"],
    )
    def test_a_same_origin_canonical_that_does_not_resolve_is_w093(
        self, tmp_path: Path, metadata: str
    ) -> None:
        page_file = metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_canonical()
        assert check_ids(messages) == ["next.W093"]
        assert messages[0].obj == str(page_file)

    @pytest.mark.parametrize(
        "metadata",
        [
            '{"canonical": "/titled/"}',
            '{"canonical": "https://other.example/nowhere/"}',
            f'{{"canonical": "{BASE}/nowhere/"}}',
            '{"canonical": True}',
        ],
        ids=["resolving", "foreign", "absolute_without_base", "self"],
    )
    def test_other_canonicals_are_silent(self, tmp_path: Path, metadata: str) -> None:
        metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_seo_canonical() == []

    @pytest.mark.parametrize(
        ("canonical", "expected"),
        [("/de/titled/", []), ("/titled/", []), ("/fr/titled/", ["next.W093"])],
        ids=["other_language", "default_language", "unlisted_language"],
    )
    @override_settings(
        ROOT_URLCONF="tests.support.urls_i18n_pages",
        USE_I18N=True,
        LANGUAGE_CODE="en",
        LANGUAGES=[("en", "English"), ("de", "German")],
    )
    def test_a_canonical_resolves_under_any_listed_language(
        self, tmp_path: Path, canonical: str, expected: list[str]
    ) -> None:
        metadata_page(tmp_path, f'{{"canonical": "{canonical}"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_ids(check_seo_canonical()) == expected

    def test_a_literal_canonical_on_a_dynamic_route_is_w094(
        self, tmp_path: Path
    ) -> None:
        page_file = metadata_page(
            tmp_path / "blog" / "[slug]", '{"canonical": "/nowhere/"}'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_canonical()
        assert check_ids(messages) == ["next.W094"]
        assert "'/blog/[slug]'" in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_a_self_canonical_on_a_dynamic_route_is_silent(
        self, tmp_path: Path
    ) -> None:
        metadata_page(tmp_path / "blog" / "[slug]", '{"canonical": True}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_seo_canonical() == []


class TestSeoAlternates:
    """`check_seo_alternates` audits an hreflang mapping."""

    def test_a_mapping_without_x_default_is_w095(self, tmp_path: Path) -> None:
        page_file = metadata_page(
            tmp_path, '{"alternates": {"languages": {"en": "/en/"}}}'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_alternates()
        assert check_ids(messages) == ["next.W095"]
        assert messages[0].obj == str(page_file)

    @pytest.mark.parametrize(
        "metadata",
        [
            '{"alternates": {"languages": {"en": "/en/"}, "x_default": "/"}}',
            '{"alternates": {"languages": {"en": "/en/", "x-default": "/"}}}',
            '{"alternates": {"languages": True}}',
        ],
        ids=["x_default_field", "x_default_key", "automatic"],
    )
    def test_a_fallback_or_the_automatic_form_is_silent(
        self, tmp_path: Path, metadata: str
    ) -> None:
        metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_seo_alternates() == []

    @override_settings(**I18N)
    def test_a_code_outside_languages_is_w096(self, tmp_path: Path) -> None:
        page_file = metadata_page(
            tmp_path,
            '{"alternates": {"languages": {"fr": "/fr/", "en": "/en/", '
            '"x-default": "/"}}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_alternates()
        assert check_ids(messages) == ["next.W096"]
        assert "'fr'" in messages[0].msg
        assert "'en'" not in messages[0].msg
        assert messages[0].obj == str(page_file)

    @override_settings(**I18N)
    def test_listed_codes_are_silent(self, tmp_path: Path) -> None:
        metadata_page(
            tmp_path,
            '{"alternates": {"languages": {"de": "/de/", "en": "/en/", '
            '"x-default": "/"}}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_seo_alternates() == []


class TestOptInTier:
    """The SEO audits run only under `check --deploy --tag seo`."""

    AUDITS = (
        check_seo_description,
        check_seo_titles,
        check_seo_canonical,
        check_seo_alternates,
    )

    @pytest.mark.parametrize("check", AUDITS, ids=lambda check: check.__name__)
    def test_an_audit_carries_the_seo_tag_as_a_deployment_check(self, check) -> None:
        register_all()
        assert set(check.tags) == {Tags.templates, NEXT, SEO}
        assert check in check_registry.deployment_checks
        assert check not in check_registry.registered_checks

    def test_the_seo_tag_selects_the_audits_only_with_deploy(
        self, tmp_path: Path
    ) -> None:
        pages = tmp_path / "pages"
        metadata_page(pages / "hello", '{"title": "Hello"}')
        register_all()
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            plain = check_registry.run_checks(tags=[SEO])
            deploy = check_registry.run_checks(
                tags=[SEO], include_deployment_checks=True
            )
        assert plain == []
        assert "next.W089" in check_ids(deploy)

    def test_the_next_tag_alone_skips_the_audits(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        metadata_page(pages / "hello", '{"title": "Hello"}')
        register_all()
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            messages = check_registry.run_checks(tags=[NEXT])
        assert "next.W089" not in check_ids(messages)
