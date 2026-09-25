from pathlib import Path

import pytest
from django.test import override_settings

from next.pages.checks import (
    check_metadata_absolute_urls,
    check_metadata_callable_returns_mapping,
    check_metadata_hreflang_patterns,
    check_metadata_noindex_canonical,
    check_metadata_registration_files,
    check_metadata_url_schemes,
    check_page_metadata_shape,
    check_single_metadata_callable,
)
from tests.pages.checks.metadata.trees import (
    BASE,
    NAMED_CALLABLE,
    metadata_page,
    scope,
    templated_page,
)
from tests.support import check_ids, importable_dir, patch_checks_router_manager


DICT_AND_CALLABLE = """
from next.pages import page

metadata = {"title": "Dict"}


@page.metadata
def meta() -> dict:
    return {"title": "Callable"}
"""


TWO_CALLABLES = """
from next.pages import page


@page.metadata
def first() -> dict:
    return {"title": "One"}


@page.metadata
def second() -> dict:
    return {"title": "Two"}
"""


IMPORTED_CALLABLE = """
from next.pages import page
from donor.helpers import donated

page.metadata(donated)
"""


class TestPageShape:
    """`check_page_metadata_shape` validates the dict of each routed page."""

    def test_a_valid_dict_is_silent(self, tmp_path: Path) -> None:
        metadata_page(
            tmp_path,
            '{"title": "Home", "twitter": {"card": "summary"}, "base": "https://a.b"}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_page_metadata_shape() == []

    def test_a_page_without_metadata_is_silent(self, tmp_path: Path) -> None:
        templated_page(tmp_path, "x = 1\n")
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_page_metadata_shape() == []

    def test_a_dict_beside_a_callable_is_e102(self, tmp_path: Path) -> None:
        page_file = templated_page(tmp_path, DICT_AND_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert check_ids(messages) == ["next.E102"]
        assert messages[0].obj == str(page_file)

    def test_a_callable_named_metadata_is_silent(self, tmp_path: Path) -> None:
        templated_page(tmp_path, NAMED_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_page_metadata_shape() == []

    def test_a_non_mapping_is_e103(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '"Home"')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert check_ids(messages) == ["next.E103"]
        assert "'str'" in messages[0].msg

    @pytest.mark.parametrize(
        ("metadata", "fragment"),
        [
            ('{"foo": 1}', "declares metadata key 'foo'"),
            ('{"og": {"bogus": 1}}', "'og.bogus'"),
            ('{"robots": {"index": "yes"}}', "'robots.index' as 'str'"),
            ('{"alternates": {"languages": [1]}}', "'alternates.languages'"),
            ('{"twitter": {"card": "huge"}}', "twitter.card 'huge'"),
        ],
        ids=["top_key", "nested_key", "nested_type", "languages", "card"],
    )
    def test_a_refused_dict_is_e104(
        self, tmp_path: Path, metadata: str, fragment: str
    ) -> None:
        page_file = metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert check_ids(messages) == ["next.E104"]
        assert fragment in messages[0].msg
        assert messages[0].obj == str(page_file)

    @pytest.mark.parametrize(
        "metadata",
        ['{"title": ""}', '{"title": {"default": ""}}', '{"title": {"absolute": ""}}'],
        ids=["text", "default", "absolute"],
    )
    def test_an_empty_title_is_e105(self, tmp_path: Path, metadata: str) -> None:
        metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_ids(check_page_metadata_shape()) == ["next.E105"]

    def test_a_page_template_without_default_is_e100(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"title": {"template": "{title} · X"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_ids(check_page_metadata_shape()) == ["next.E100"]

    def test_a_page_base_that_is_no_origin_is_e101(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"base": "https://acme.example/blog"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_ids(check_page_metadata_shape()) == ["next.E101"]

    def test_an_ancestor_error_is_reported_on_the_ancestor_only(
        self, tmp_path: Path
    ) -> None:
        parent = metadata_page(tmp_path, '{"foo": 1}')
        metadata_page(tmp_path / "child", '{"title": "Child"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert [(m.id, m.obj) for m in messages] == [("next.E104", str(parent))]


class TestRegistrationFiles:
    """`check_metadata_registration_files` catches a callable bound elsewhere."""

    def test_a_callable_declared_in_its_page_is_silent(self, tmp_path: Path) -> None:
        templated_page(
            tmp_path,
            "from next.pages import page\n\n"
            "@page.metadata\n"
            "def meta() -> dict:\n"
            "    return {}\n",
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_registration_files() == []

    def test_a_callable_imported_from_a_helper_is_e106(self, tmp_path: Path) -> None:
        donor = tmp_path / "donor"
        donor.mkdir()
        (donor / "__init__.py").write_text("")
        helper = donor / "helpers.py"
        helper.write_text("def donated() -> dict:\n    return {'title': 'x'}\n")
        page_file = templated_page(tmp_path, IMPORTED_CALLABLE)
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            importable_dir(tmp_path),
        ):
            messages = check_metadata_registration_files()
        assert check_ids(messages) == ["next.E106"]
        assert "@page.metadata" in messages[0].msg
        assert "donated" in messages[0].msg
        assert str(helper) in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_a_decorator_run_outside_a_page_is_e106(self, tmp_path: Path) -> None:
        donor = tmp_path / "donor"
        donor.mkdir()
        (donor / "__init__.py").write_text("")
        helper = donor / "helpers.py"
        helper.write_text(
            "from next.pages import page\n\n"
            "@page.metadata\n"
            "def stranded() -> dict:\n"
            "    return {}\n"
        )
        templated_page(tmp_path, "import donor.helpers\n")
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            importable_dir(tmp_path),
        ):
            messages = check_metadata_registration_files()
        assert check_ids(messages) == ["next.E106"]
        assert "is not a page.py" in messages[0].msg
        assert messages[0].obj == str(helper)


class TestSingleCallable:
    """`check_single_metadata_callable` reports two callables on one page."""

    def test_two_callables_are_e107(self, tmp_path: Path) -> None:
        page_file = templated_page(tmp_path, TWO_CALLABLES)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_single_metadata_callable()
        assert check_ids(messages) == ["next.E107"]
        assert "first, second" in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_one_callable_is_silent(self, tmp_path: Path) -> None:
        templated_page(
            tmp_path,
            "from next.pages import page\n\n"
            "@page.metadata\n"
            "def meta() -> dict:\n"
            "    return {}\n",
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_single_metadata_callable() == []


class TestCallableReturnsMapping:
    """`check_metadata_callable_returns_mapping` reads the return annotation."""

    @pytest.mark.parametrize(
        "annotation",
        ["-> dict", "-> MetadataDict", '-> "dict[str, object]"', ""],
        ids=["dict", "typed_dict", "quoted_generic", "unannotated"],
    )
    def test_a_dict_like_or_absent_annotation_is_silent(
        self, tmp_path: Path, annotation: str
    ) -> None:
        templated_page(
            tmp_path,
            "from next.pages import page\n"
            "from next.pages.metadata import MetadataDict\n\n"
            "@page.metadata\n"
            f"def meta() {annotation}:\n"
            "    return {}\n",
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_callable_returns_mapping() == []

    def test_a_non_mapping_annotation_is_e108(self, tmp_path: Path) -> None:
        page_file = templated_page(
            tmp_path,
            "from next.pages import page\n\n"
            "@page.metadata\n"
            "def meta() -> str:\n"
            "    return 'x'\n",
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_callable_returns_mapping()
        assert check_ids(messages) == ["next.E108"]
        assert "meta" in messages[0].msg
        assert "str" in messages[0].msg
        assert messages[0].obj == str(page_file)


class TestUrlSchemes:
    """`check_metadata_url_schemes` rejects schemes outside http and https."""

    @pytest.mark.parametrize(
        ("metadata", "field"),
        [
            ('{"canonical": "javascript:alert(1)"}', "'canonical'"),
            ('{"og": {"url": "ftp://x/"}}', "'og.url'"),
            ('{"og": {"images": ["/a.png", "ftp://x/a.png"]}}', "'og.images[1].url'"),
            ('{"twitter": {"images": ["mailto:x"]}}', "'twitter.images[0]'"),
            ('{"alternates": {"x_default": "gopher://x"}}', "'alternates.x_default'"),
            (
                '{"alternates": {"languages": {"de": "data:x"}}}',
                "'alternates.languages.de'",
            ),
        ],
        ids=["canonical", "og_url", "og_image", "twitter", "x_default", "language"],
    )
    def test_a_foreign_scheme_is_e109(
        self, tmp_path: Path, metadata: str, field: str
    ) -> None:
        page_file = metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_url_schemes()
        assert check_ids(messages) == ["next.E109"]
        assert field in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_http_and_relative_urls_are_silent(self, tmp_path: Path) -> None:
        metadata_page(
            tmp_path,
            '{"canonical": "/x/", "og": {"url": "https://a.b/x/", '
            '"images": ["http://a.b/i.png", "i.png"]}, '
            '"alternates": {"languages": True}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_url_schemes() == []


class TestAbsoluteUrls:
    """`check_metadata_absolute_urls` wants a base behind root-relative URLs."""

    @pytest.mark.parametrize(
        ("metadata", "field"),
        [
            ('{"canonical": "/x/"}', "canonical"),
            ('{"og": {"images": ["/i.png"]}}', "og.images[0].url"),
            ('{"twitter": {"images": ["/t.png"]}}', "twitter.images[0]"),
        ],
        ids=["canonical", "og_image", "twitter_image"],
    )
    @override_settings(DEBUG=False)
    def test_a_root_relative_url_without_base_is_w086(
        self, tmp_path: Path, metadata: str, field: str
    ) -> None:
        page_file = metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_absolute_urls()
        assert check_ids(messages) == ["next.W086"]
        assert field in messages[0].msg
        assert messages[0].obj == str(page_file)

    @override_settings(DEBUG=False)
    def test_a_base_in_the_chain_is_silent(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"canonical": "/x/", "og": {"images": ["/i.png"]}}')
        with (
            override_settings(NEXT_FRAMEWORK=scope(DEFAULTS={"base": BASE})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_metadata_absolute_urls() == []

    @override_settings(DEBUG=False)
    def test_absolute_and_protocol_relative_urls_are_silent(
        self, tmp_path: Path
    ) -> None:
        metadata_page(
            tmp_path,
            '{"canonical": "https://a.b/x/", "og": {"images": ["//cdn.a.b/i.png"]}, '
            '"alternates": {"x_default": "/x/"}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_absolute_urls() == []

    @override_settings(DEBUG=True)
    def test_debug_is_silent(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"canonical": "/x/"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_absolute_urls() == []


class TestHreflangPatterns:
    """`check_metadata_hreflang_patterns` wants `i18n_patterns()` behind `True`."""

    def test_languages_true_without_prefix_patterns_is_w087(
        self, tmp_path: Path
    ) -> None:
        page_file = metadata_page(tmp_path, '{"alternates": {"languages": True}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_hreflang_patterns()
        assert check_ids(messages) == ["next.W087"]
        assert "'next.urls'" in messages[0].msg
        assert messages[0].obj == str(page_file)

    @override_settings(ROOT_URLCONF="tests.support.urls_i18n_pages")
    def test_languages_true_under_prefix_patterns_is_silent(
        self, tmp_path: Path
    ) -> None:
        metadata_page(tmp_path, '{"alternates": {"languages": True}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_hreflang_patterns() == []

    def test_a_mapping_is_silent(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"alternates": {"languages": {"en": "/en/"}}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_hreflang_patterns() == []


class TestNoindexCanonical:
    """`check_metadata_noindex_canonical` pairs noindex with a foreign canonical."""

    @pytest.mark.parametrize(
        "metadata",
        [
            '{"robots": {"index": False}, "canonical": "https://other.example/x/"}',
            '{"robots": "noindex, follow", "canonical": "https://other.example/x/"}',
            (
                '{"robots": {"index": False}, "base": "https://acme.example", '
                '"canonical": "https://other.example/x/"}'
            ),
        ],
        ids=["flags", "string", "other_host_than_base"],
    )
    def test_noindex_with_a_foreign_canonical_is_w088(
        self, tmp_path: Path, metadata: str
    ) -> None:
        page_file = metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_noindex_canonical()
        assert check_ids(messages) == ["next.W088"]
        assert "https://other.example/x/" in messages[0].msg
        assert messages[0].obj == str(page_file)

    @pytest.mark.parametrize(
        "metadata",
        [
            '{"robots": {"index": True}, "canonical": "https://other.example/x/"}',
            '{"robots": {"index": False}, "canonical": "/x/"}',
            '{"robots": {"index": False}, "canonical": True}',
            (
                '{"robots": {"index": False}, "base": "https://acme.example", '
                '"canonical": "https://acme.example/x/"}'
            ),
            '{"robots": "nofollow", "canonical": "https://other.example/x/"}',
        ],
        ids=["indexed", "relative", "self", "same_host", "string_without_noindex"],
    )
    def test_other_pairings_are_silent(self, tmp_path: Path, metadata: str) -> None:
        metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_noindex_canonical() == []
