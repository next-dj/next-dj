from pathlib import Path

import pytest
from django.test import override_settings

from next.pages.checks import (
    check_metadata_callable_returns_mapping,
    check_metadata_enum_values,
    check_metadata_hreflang_patterns,
    check_metadata_noindex_canonical,
    check_metadata_parent_parameter,
    check_metadata_registration_files,
    check_page_metadata_shape,
)
from next.testing import override_next_settings
from tests.pages.checks.metadata.trees import (
    NAMED_CALLABLE,
    metadata_page,
    templated_page,
)
from tests.support import (
    BASE,
    I18N_URLCONF,
    check_ids,
    importable_dir,
    patch_checks_router_manager,
)


DICT_AND_CALLABLE = """
from next.pages import page

metadata = {"title": "Dict"}


@page.metadata
def meta() -> dict:
    return {"title": "Callable"}
"""


PARENT_PARAMETER = """
from next.pages import page
from next.pages.metadata import Metadata


@page.metadata
def meta(parent: Metadata, slug: str) -> dict:
    return {"title": slug}
"""


QUOTED_PARENT_PARAMETER = """
from next.pages import page


@page.metadata
def meta(parent: "Metadata") -> dict:
    return {}
"""


LAZY_FOREIGN_URLS = """
from django.utils.functional import lazy

metadata = {
    "canonical": lazy(str, str)("ftp://x/"),
    "og": {"url": lazy(str, str)("ftp://x/")},
}
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
            '{"title": "Home", "twitter": {"card": "summary"}, "canonical": True}',
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

    def test_a_callable_named_metadata_is_e102(self, tmp_path: Path) -> None:
        page_file = templated_page(tmp_path, NAMED_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert check_ids(messages) == ["next.E102"]
        assert "Rename the callable" in messages[0].msg
        assert messages[0].obj == str(page_file)

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
            ('{"canonical": "javascript:alert(1)"}', "the URL 'javascript:alert(1)'"),
            ('{"og": {"images": ["/a.png", "ftp://x/"]}}', "'og.images[1]' as the URL"),
            ('{"canonical": False}', "write RESET to drop an inherited canonical"),
            (
                '{"alternates": {"languages": {"x-default": "/"}, "x_default": "/"}}',
                "and an 'x-default' language, keep one",
            ),
            ('{"jsonld": {"rating": float("nan")}}', "'jsonld.rating' as nan"),
        ],
        ids=[
            "top_key",
            "nested_key",
            "nested_type",
            "languages",
            "card",
            "url_scheme",
            "image_scheme",
            "canonical_false",
            "two_x_defaults",
            "jsonld_nan",
        ],
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

    def test_two_jsonld_objects_with_one_id_is_w108(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"jsonld": [{"@id": "#p"}, {"@id": "#p"}]}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert check_ids(messages) == ["next.W103"]
        assert "'/#p'" in messages[0].msg

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


class TestParentParameter:
    """`check_metadata_parent_parameter` flags a parameter annotated `Metadata`."""

    @pytest.mark.parametrize(
        "source", [PARENT_PARAMETER, QUOTED_PARENT_PARAMETER], ids=["class", "quoted"]
    )
    def test_a_metadata_parameter_is_e121(self, tmp_path: Path, source: str) -> None:
        page_file = templated_page(tmp_path, source)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_parent_parameter()
        assert check_ids(messages) == ["next.E121"]
        assert "'parent'" in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_other_parameters_and_the_dict_form_are_silent(
        self, tmp_path: Path
    ) -> None:
        templated_page(
            tmp_path,
            "from next.pages import page\n\n"
            "@page.metadata\n"
            "def meta(slug: str, request) -> dict:\n"
            "    return {}\n",
        )
        metadata_page(tmp_path / "child", '{"title": "Child"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_parent_parameter() == []


class TestUrlSchemes:
    """A foreign scheme fails the shape, so the fold carries only web URLs."""

    def test_a_foreign_scheme_is_a_shape_error(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"canonical": "javascript:alert(1)"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_ids(check_page_metadata_shape()) == ["next.E104"]

    def test_a_lazy_url_is_left_to_the_render_that_forces_it(
        self, tmp_path: Path
    ) -> None:
        templated_page(tmp_path, LAZY_FOREIGN_URLS)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_page_metadata_shape() == []
            assert check_metadata_noindex_canonical() == []


class TestHreflangPatterns:
    """`check_metadata_hreflang_patterns` requires `i18n_patterns()` for `True`."""

    def test_languages_true_without_prefix_patterns_is_w087(
        self, tmp_path: Path
    ) -> None:
        page_file = metadata_page(tmp_path, '{"alternates": {"languages": True}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_hreflang_patterns()
        assert check_ids(messages) == ["next.W087"]
        assert "'next.urls'" in messages[0].msg
        assert messages[0].obj == str(page_file)

    @override_settings(ROOT_URLCONF=I18N_URLCONF)
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
            '{"robots": "NONE", "canonical": "https://other.example/x/"}',
        ],
        ids=["flags", "none_token"],
    )
    @pytest.mark.parametrize("site", [None, BASE], ids=["no_site", "other_site"])
    def test_noindex_with_a_foreign_canonical_is_w088(
        self, tmp_path: Path, metadata: str, site: str | None
    ) -> None:
        page_file = metadata_page(tmp_path, metadata)
        with (
            override_next_settings(SITE={"URL": site}),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
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
            '{"robots": {"index": False}, "canonical": "https://acme.example/x/"}',
            '{"robots": "nofollow", "canonical": "https://other.example/x/"}',
        ],
        ids=["indexed", "relative", "self", "same_host", "string_without_noindex"],
    )
    def test_other_pairings_are_silent(self, tmp_path: Path, metadata: str) -> None:
        metadata_page(tmp_path, metadata)
        with (
            override_next_settings(SITE={"URL": BASE}),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_metadata_noindex_canonical() == []


class TestEnumValues:
    """`check_metadata_enum_values` keeps each enum key inside its set."""

    def test_valid_values_are_silent(self, tmp_path: Path) -> None:
        metadata_page(
            tmp_path,
            '{"og": {"determiner": "the"}, "color_scheme": "only light", '
            '"viewport": {"viewport_fit": "cover", "interactive_widget": '
            '"resizes-content"}, "twitter": {"card": "player", "player": '
            '{"url": "/p", "width": 1, "height": 1}}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_enum_values() == []

    @pytest.mark.parametrize(
        ("metadata", "fragment"),
        [
            ('{"og": {"determiner": "some"}}', "og.determiner 'some'"),
            ('{"viewport": {"viewport_fit": "fill"}}', "viewport.viewport_fit 'fill'"),
            (
                '{"viewport": {"interactive_widget": "x"}}',
                "viewport.interactive_widget 'x'",
            ),
            ('{"color_scheme": "light sepia"}', "color_scheme 'light sepia'"),
            ('{"twitter": {"card": "player"}}', "without twitter.player"),
        ],
        ids=["determiner", "viewport_fit", "interactive_widget", "scheme", "player"],
    )
    def test_a_value_outside_its_set_is_e104(
        self, tmp_path: Path, metadata: str, fragment: str
    ) -> None:
        metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_enum_values()
        assert check_ids(messages) == ["next.E104"]
        assert fragment in messages[0].msg

    def test_a_string_viewport_is_not_read_for_enums(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"viewport": "viewport-fit=fill"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_enum_values() == []

    def test_the_defaults_are_checked(self) -> None:
        framework = {"METADATA": {"DEFAULTS": {"og": {"determiner": "x"}}}}
        with override_settings(NEXT_FRAMEWORK=framework):
            messages = check_metadata_enum_values()
        assert check_ids(messages) == ["next.E104"]
