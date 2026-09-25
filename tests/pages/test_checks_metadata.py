from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.checks import Tags
from django.core.checks.registry import registry as check_registry
from django.test import override_settings

from next.checks import NEXT, SEO, register_all, reset_check_caches
from next.pages.checks import (
    check_metadata_absolute_urls,
    check_metadata_callable_returns_mapping,
    check_metadata_hreflang_patterns,
    check_metadata_noindex_canonical,
    check_metadata_registration_files,
    check_metadata_settings_scope,
    check_metadata_tag_rendered,
    check_metadata_title_templates,
    check_metadata_url_schemes,
    check_page_metadata_shape,
    check_seo_alternates,
    check_seo_canonical,
    check_seo_description,
    check_seo_titles,
    check_single_metadata_callable,
)
from next.pages.checks.metadata import loaded_metadata_pages
from next.pages.metadata.defaults import SITE_SOURCE
from tests.support import (
    file_router_config_entry,
    importable_dir,
    patch_checks_router_manager,
)


I18N = {
    "USE_I18N": True,
    "LANGUAGES": [("en", "English"), ("de", "German")],
    "LANGUAGE_CODE": "en",
}
BASE = "https://acme.example"
DESCRIPTION = "A description long enough to pass the audit without any complaint."
PER_LANGUAGE_TEMPLATE = """
from django.utils.functional import lazy
from django.utils.translation import get_language


def _text():
    if (get_language() or "").startswith("de"):
        return "{title} · Seite"
    return "{title} · Site"


metadata = {"title": {"template": lazy(_text, str)(), "default": "Site"}}
"""
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
NAMED_CALLABLE = """
from next.pages import page


@page.metadata
def metadata() -> dict:
    return {"title": "Named"}
"""
OWN_CALLABLE = """
from next.pages import page


@page.metadata
def meta() -> dict:
    return {"title": "Dynamic"}
"""
INHERITED_CALLABLE = """
from next.pages import page


@page.metadata(inherit=True)
def meta() -> dict:
    return {"title": "Inherited"}
"""
IMPORTED_CALLABLE = """
from next.pages import page
from donor.helpers import donated

page.metadata(donated)
"""
COMPONENTS = [
    {
        "BACKEND": "next.components.FileComponentsBackend",
        "DIRS": [],
        "COMPONENTS_DIR": "_components",
    }
]


@pytest.fixture(autouse=True)
def _fresh_check_state() -> Iterator[None]:
    reset_check_caches()
    yield
    reset_check_caches()


def _ids(messages: list) -> list[str]:
    return [m.id for m in messages]


def _write_page(directory: Path, source: str, body: str | None = "<p>x</p>") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(source)
    if body is not None:
        (directory / "template.djx").write_text(body)
    return page_file


def _metadata_page(directory: Path, metadata: str) -> Path:
    return _write_page(directory, f"metadata = {metadata}\n")


def _scope(**metadata: object) -> dict[str, object]:
    return {"METADATA": metadata}


def _framework(pages: Path) -> dict[str, object]:
    return {
        "PAGE_BACKENDS": [file_router_config_entry(pages_dir=pages)],
        "COMPONENT_BACKENDS": COMPONENTS,
    }


class TestSettingsScope:
    """`check_metadata_settings_scope` reads the raw `METADATA` scope."""

    def test_a_valid_scope_is_silent(self) -> None:
        defaults = {
            "base": BASE,
            "title": {"template": "{title} · Acme", "default": "Acme"},
            "description": DESCRIPTION,
        }
        with override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults)):
            assert check_metadata_settings_scope() == []

    def test_an_absent_scope_is_silent(self) -> None:
        with override_settings(NEXT_FRAMEWORK={}):
            assert check_metadata_settings_scope() == []

    def test_a_non_dict_scope_is_left_to_the_conf_check(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"METADATA": "x"}):
            assert check_metadata_settings_scope() == []

    def test_an_unknown_option_is_e035(self) -> None:
        with override_settings(NEXT_FRAMEWORK=_scope(BOGUS=1)):
            messages = check_metadata_settings_scope()
        assert _ids(messages) == ["next.E035"]
        assert "NEXT_FRAMEWORK['METADATA']" in messages[0].msg
        assert "'BOGUS'" in messages[0].msg

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
        with override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults)):
            messages = check_metadata_settings_scope()
        assert _ids(messages) == ["next.E098"]
        assert fragment in messages[0].msg
        assert SITE_SOURCE in messages[0].msg

    def test_a_template_without_default_is_e100(self) -> None:
        defaults = {"title": {"template": "{title} · Acme"}}
        with override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults)):
            messages = check_metadata_settings_scope()
        assert _ids(messages) == ["next.E100"]
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
    )
    def test_a_base_that_is_no_origin_is_e101(self, base: str) -> None:
        with override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS={"base": base})):
            messages = check_metadata_settings_scope()
        assert _ids(messages) == ["next.E101"]
        assert repr(base) in messages[0].msg

    @pytest.mark.parametrize("base", [BASE, f"{BASE}/", "http://localhost:8000"])
    def test_an_origin_base_is_silent(self, base: str) -> None:
        with override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS={"base": base})):
            assert check_metadata_settings_scope() == []

    def test_an_empty_default_title_is_e105(self) -> None:
        defaults = {"title": {"default": ""}}
        with override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults)):
            messages = check_metadata_settings_scope()
        assert _ids(messages) == ["next.E105"]

    def test_every_settings_finding_is_reported_together(self) -> None:
        defaults = {"title": {"template": "{title}"}, "base": "x"}
        with override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults, NOPE=1)):
            messages = check_metadata_settings_scope()
        assert _ids(messages) == ["next.E035", "next.E100", "next.E101"]


class TestTitleTemplates:
    """`check_metadata_title_templates` parses both tiers under every language."""

    def test_valid_templates_are_silent(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, '{"title": {"template": "{title} · {site_name}"}}')
        defaults = {"title": {"template": "{title} · Acme", "default": "Acme"}}
        with (
            override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_metadata_title_templates() == []

    @pytest.mark.parametrize(
        ("template", "fragment"),
        [
            ("{title} · Acme", "names the placeholder 'title'"),
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
            override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert _ids(messages) == ["next.E099"]
        assert fragment in messages[0].msg
        assert SITE_SOURCE in messages[0].msg
        assert "under the language" not in messages[0].msg

    def test_a_page_template_the_parser_refuses_is_e099(self, tmp_path: Path) -> None:
        page_file = _metadata_page(
            tmp_path, '{"title": {"template": "{nope}", "default": "X"}}'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_title_templates()
        assert _ids(messages) == ["next.E099"]
        assert str(page_file) in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_a_template_broken_in_one_language_names_that_language(
        self, tmp_path: Path
    ) -> None:
        _write_page(tmp_path, PER_LANGUAGE_TEMPLATE)
        with (
            override_settings(**I18N),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert _ids(messages) == ["next.E099"]
        assert "under the language 'de'" in messages[0].msg
        assert "'{title} · Seite'" in messages[0].msg
        assert "'en'" not in messages[0].msg

    def test_a_template_valid_in_the_only_language_is_silent(
        self, tmp_path: Path
    ) -> None:
        _write_page(tmp_path, PER_LANGUAGE_TEMPLATE)
        with (
            override_settings(USE_I18N=False),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_metadata_title_templates() == []

    def test_one_finding_under_every_language_is_reported_once(
        self, tmp_path: Path
    ) -> None:
        defaults = {"title": {"template": "{title}", "default": "Acme"}}
        with (
            override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults), **I18N),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert _ids(messages) == ["next.E099"]
        assert "under the language" not in messages[0].msg

    def test_a_finding_under_some_languages_names_them(self, tmp_path: Path) -> None:
        _write_page(tmp_path, PER_LANGUAGE_TEMPLATE)
        languages = [("en", "English"), ("de", "German"), ("de-at", "Austrian")]
        with (
            override_settings(**{**I18N, "LANGUAGES": languages}),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert _ids(messages) == ["next.E099"]
        assert "under the languages 'de', 'de-at'" in messages[0].msg

    def test_a_settings_template_without_title_is_w084(self, tmp_path: Path) -> None:
        defaults = {"title": {"template": "Acme", "default": "Acme"}}
        with (
            override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            messages = check_metadata_title_templates()
        assert _ids(messages) == ["next.W084"]
        assert messages[0].hint == (
            "Write {title}, the %s placeholder of Next.js is not substituted here."
        )

    def test_a_page_template_without_title_is_w084(self, tmp_path: Path) -> None:
        page_file = _metadata_page(
            tmp_path, '{"title": {"template": "%s · Site", "default": "Site"}}'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_title_templates()
        assert _ids(messages) == ["next.W084"]
        assert messages[0].obj == str(page_file)

    def test_a_refused_defaults_tier_contributes_no_template(
        self, tmp_path: Path
    ) -> None:
        with (
            override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS={"title": "Acme"})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_metadata_title_templates() == []


class TestPageShape:
    """`check_page_metadata_shape` validates the dict of each routed page."""

    def test_a_valid_dict_is_silent(self, tmp_path: Path) -> None:
        _metadata_page(
            tmp_path,
            '{"title": "Home", "twitter": {"card": "summary"}, "base": "https://a.b"}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_page_metadata_shape() == []

    def test_a_page_without_metadata_is_silent(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "x = 1\n")
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_page_metadata_shape() == []

    def test_a_dict_beside_a_callable_is_e102(self, tmp_path: Path) -> None:
        page_file = _write_page(tmp_path, DICT_AND_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert _ids(messages) == ["next.E102"]
        assert messages[0].obj == str(page_file)

    def test_a_callable_named_metadata_is_silent(self, tmp_path: Path) -> None:
        _write_page(tmp_path, NAMED_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_page_metadata_shape() == []

    def test_a_non_mapping_is_e103(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, '"Home"')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert _ids(messages) == ["next.E103"]
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
        page_file = _metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert _ids(messages) == ["next.E104"]
        assert fragment in messages[0].msg
        assert messages[0].obj == str(page_file)

    @pytest.mark.parametrize(
        "metadata",
        ['{"title": ""}', '{"title": {"default": ""}}', '{"title": {"absolute": ""}}'],
        ids=["text", "default", "absolute"],
    )
    def test_an_empty_title_is_e105(self, tmp_path: Path, metadata: str) -> None:
        _metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert _ids(check_page_metadata_shape()) == ["next.E105"]

    def test_a_page_template_without_default_is_e100(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, '{"title": {"template": "{title} · X"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert _ids(check_page_metadata_shape()) == ["next.E100"]

    def test_a_page_base_that_is_no_origin_is_e101(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, '{"base": "https://acme.example/blog"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert _ids(check_page_metadata_shape()) == ["next.E101"]

    def test_an_ancestor_error_is_reported_on_the_ancestor_only(
        self, tmp_path: Path
    ) -> None:
        parent = _metadata_page(tmp_path, '{"foo": 1}')
        _metadata_page(tmp_path / "child", '{"title": "Child"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_page_metadata_shape()
        assert [(m.id, m.obj) for m in messages] == [("next.E104", str(parent))]


class TestRegistrationFiles:
    """`check_metadata_registration_files` catches a callable bound elsewhere."""

    def test_a_callable_declared_in_its_page_is_silent(self, tmp_path: Path) -> None:
        _write_page(
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
        page_file = _write_page(tmp_path, IMPORTED_CALLABLE)
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            importable_dir(tmp_path),
        ):
            messages = check_metadata_registration_files()
        assert _ids(messages) == ["next.E106"]
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
        _write_page(tmp_path, "import donor.helpers\n")
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            importable_dir(tmp_path),
        ):
            messages = check_metadata_registration_files()
        assert _ids(messages) == ["next.E106"]
        assert "is not a page.py" in messages[0].msg
        assert messages[0].obj == str(helper)


class TestSingleCallable:
    """`check_single_metadata_callable` reports two callables on one page."""

    def test_two_callables_are_e107(self, tmp_path: Path) -> None:
        page_file = _write_page(tmp_path, TWO_CALLABLES)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_single_metadata_callable()
        assert _ids(messages) == ["next.E107"]
        assert "first, second" in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_one_callable_is_silent(self, tmp_path: Path) -> None:
        _write_page(
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
        _write_page(
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
        page_file = _write_page(
            tmp_path,
            "from next.pages import page\n\n"
            "@page.metadata\n"
            "def meta() -> str:\n"
            "    return 'x'\n",
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_callable_returns_mapping()
        assert _ids(messages) == ["next.E108"]
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
        page_file = _metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_url_schemes()
        assert _ids(messages) == ["next.E109"]
        assert field in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_http_and_relative_urls_are_silent(self, tmp_path: Path) -> None:
        _metadata_page(
            tmp_path,
            '{"canonical": "/x/", "og": {"url": "https://a.b/x/", '
            '"images": ["http://a.b/i.png", "i.png"]}, '
            '"alternates": {"languages": True}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_url_schemes() == []


class TestTagRendered:
    """`check_metadata_tag_rendered` walks the composition and its components."""

    def _project(self, tmp_path: Path, layout: str) -> tuple[Path, Path]:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text(layout)
        page_file = _metadata_page(pages / "hello", '{"title": "Hello"}')
        return pages, page_file

    def _component(self, pages: Path, name: str, body: str) -> None:
        folder = pages / "_components" / name
        folder.mkdir(parents=True)
        (folder / "component.djx").write_text(body)

    def test_a_tag_in_the_layout_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, "<html><head>{% metadata %}</head>{% template %}</html>"
        )
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_a_tag_inside_a_component_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "head" %}{% template %}</html>'
        )
        self._component(pages, "head", "<head>{% metadata %}</head>")
        with override_settings(NEXT_FRAMEWORK=_framework(pages)):
            assert check_metadata_tag_rendered() == []

    def test_a_tag_two_components_deep_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path,
            '<html>{% #component "head" %}x{% /component %}{% template %}</html>',
        )
        self._component(pages, "head", '<head>{% component "seo" %}</head>')
        self._component(pages, "seo", "{% metadata %}")
        with override_settings(NEXT_FRAMEWORK=_framework(pages)):
            assert check_metadata_tag_rendered() == []

    def test_no_tag_anywhere_is_w085(self, tmp_path: Path) -> None:
        pages, page_file = self._project(
            tmp_path, '<html>{% component "head" %}{% template %}</html>'
        )
        self._component(pages, "head", '<head><meta charset="utf-8"></head>')
        with override_settings(NEXT_FRAMEWORK=_framework(pages)):
            messages = check_metadata_tag_rendered()
        assert _ids(messages) == ["next.W085"]
        assert "{% metadata %}" in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_no_tag_and_no_component_is_w085(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(tmp_path, "<html>{% template %}</html>")
        with patch_checks_router_manager(pages_directory=pages):
            assert _ids(check_metadata_tag_rendered()) == ["next.W085"]

    def test_a_page_without_metadata_is_silent(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text("<html>{% template %}</html>")
        _write_page(pages / "hello", "x = 1\n")
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_metadata_on_an_ancestor_counts_as_declared(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text("<html>{% template %}</html>")
        _metadata_page(pages, '{"title": "Root"}')
        child = _write_page(pages / "child", "x = 1\n")
        with patch_checks_router_manager(pages_directory=pages):
            messages = check_metadata_tag_rendered()
        assert sorted(m.obj for m in messages) == sorted(
            [str(pages / "page.py"), str(child)]
        )

    def test_an_unresolved_component_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "head" %}{% template %}</html>'
        )
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_a_render_page_is_silent(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text("<html>{% template %}</html>")
        _write_page(
            pages / "hello",
            'metadata = {"title": "Hello"}\n\n'
            "def render(request):\n"
            "    return '<p>x</p>'\n",
            body=None,
        )
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_a_composition_that_does_not_compile_is_silent(
        self, tmp_path: Path
    ) -> None:
        pages, _page_file = self._project(tmp_path, "<html>{% if %}{% template %}")
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_a_component_that_does_not_compile_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "head" %}{% template %}</html>'
        )
        self._component(pages, "head", "{% if %}")
        with override_settings(NEXT_FRAMEWORK=_framework(pages)):
            assert check_metadata_tag_rendered() == []

    def test_a_component_cycle_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "a" %}{% template %}</html>'
        )
        self._component(pages, "a", '{% component "b" %}')
        self._component(pages, "b", '{% component "a" %}')
        with override_settings(NEXT_FRAMEWORK=_framework(pages)):
            assert check_metadata_tag_rendered() == []

    def test_a_descent_past_the_depth_cap_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "c0" %}{% template %}</html>'
        )
        for index in range(9):
            self._component(pages, f"c{index}", f'{{% component "c{index + 1}" %}}')
        self._component(pages, "c9", "<p>leaf</p>")
        with override_settings(NEXT_FRAMEWORK=_framework(pages)):
            assert check_metadata_tag_rendered() == []


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
        page_file = _metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_absolute_urls()
        assert _ids(messages) == ["next.W086"]
        assert field in messages[0].msg
        assert messages[0].obj == str(page_file)

    @override_settings(DEBUG=False)
    def test_a_base_in_the_chain_is_silent(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, '{"canonical": "/x/", "og": {"images": ["/i.png"]}}')
        with (
            override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS={"base": BASE})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_metadata_absolute_urls() == []

    @override_settings(DEBUG=False)
    def test_absolute_and_protocol_relative_urls_are_silent(
        self, tmp_path: Path
    ) -> None:
        _metadata_page(
            tmp_path,
            '{"canonical": "https://a.b/x/", "og": {"images": ["//cdn.a.b/i.png"]}, '
            '"alternates": {"x_default": "/x/"}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_absolute_urls() == []

    @override_settings(DEBUG=True)
    def test_debug_is_silent(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, '{"canonical": "/x/"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_absolute_urls() == []


class TestHreflangPatterns:
    """`check_metadata_hreflang_patterns` wants `i18n_patterns()` behind `True`."""

    def test_languages_true_without_prefix_patterns_is_w087(
        self, tmp_path: Path
    ) -> None:
        page_file = _metadata_page(tmp_path, '{"alternates": {"languages": True}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_hreflang_patterns()
        assert _ids(messages) == ["next.W087"]
        assert "'next.urls'" in messages[0].msg
        assert messages[0].obj == str(page_file)

    @override_settings(ROOT_URLCONF="tests.support.i18n_urls")
    def test_languages_true_under_prefix_patterns_is_silent(
        self, tmp_path: Path
    ) -> None:
        _metadata_page(tmp_path, '{"alternates": {"languages": True}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_hreflang_patterns() == []

    def test_a_mapping_is_silent(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, '{"alternates": {"languages": {"en": "/en/"}}}')
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
        page_file = _metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_noindex_canonical()
        assert _ids(messages) == ["next.W088"]
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
        _metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_noindex_canonical() == []


class TestSeoDescription:
    """`check_seo_description` audits presence and length."""

    def test_a_missing_description_is_w089(self, tmp_path: Path) -> None:
        page_file = _metadata_page(tmp_path, '{"title": "Home"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_description()
        assert _ids(messages) == ["next.W089"]
        assert messages[0].obj == str(page_file)

    def test_a_missing_description_is_silent_when_not_required(
        self, tmp_path: Path
    ) -> None:
        _metadata_page(tmp_path, '{"title": "Home"}')
        checks = {"REQUIRE_DESCRIPTION": False}
        with (
            override_settings(NEXT_FRAMEWORK=_scope(CHECKS=checks)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_seo_description() == []

    def test_a_description_from_the_settings_tier_is_silent(
        self, tmp_path: Path
    ) -> None:
        _metadata_page(tmp_path, '{"title": "Home"}')
        defaults = {"description": DESCRIPTION}
        with (
            override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS=defaults)),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_seo_description() == []

    @pytest.mark.parametrize(
        "description", ["Too short.", "x" * 161], ids=["short", "long"]
    )
    def test_a_description_outside_the_window_is_w092(
        self, tmp_path: Path, description: str
    ) -> None:
        _metadata_page(tmp_path, f'{{"description": "{description}"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_description()
        assert _ids(messages) == ["next.W092"]
        assert f"{len(description)} characters" in messages[0].msg

    def test_a_raised_maximum_widens_the_window(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, f'{{"description": "{"x" * 161}"}}')
        with (
            override_settings(NEXT_FRAMEWORK=_scope(CHECKS={"DESCRIPTION_MAX": 200})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_seo_description() == []

    def test_an_unusable_threshold_keeps_the_default(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, f'{{"description": "{"x" * 161}"}}')
        with (
            override_settings(NEXT_FRAMEWORK=_scope(CHECKS={"DESCRIPTION_MAX": True})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert _ids(check_seo_description()) == ["next.W092"]


class TestSeoTitles:
    """`check_seo_titles` audits duplicates and length."""

    def test_two_pages_with_one_title_are_one_w090(self, tmp_path: Path) -> None:
        first = _metadata_page(tmp_path / "a", '{"title": "Same"}')
        _metadata_page(tmp_path / "b", '{"title": "Same"}')
        _metadata_page(tmp_path / "c", '{"title": "Other"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_titles()
        assert _ids(messages) == ["next.W090"]
        assert "'/a'" in messages[0].msg
        assert "'/b'" in messages[0].msg
        assert "'/c'" not in messages[0].msg
        assert messages[0].obj == str(first)

    def test_a_templated_duplicate_is_compared_after_the_fold(
        self, tmp_path: Path
    ) -> None:
        _metadata_page(
            tmp_path, '{"title": {"template": "{title} · X", "default": "X"}}'
        )
        _metadata_page(tmp_path / "a", '{"title": "Same"}')
        _metadata_page(tmp_path / "b", '{"title": {"absolute": "Same · X"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_titles()
        assert _ids(messages) == ["next.W090"]
        assert "'Same · X'" in messages[0].msg

    def test_distinct_titles_are_silent(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path / "a", '{"title": "One"}')
        _metadata_page(tmp_path / "b", '{"title": "Two"}')
        _write_page(tmp_path / "c", "x = 1\n")
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_seo_titles() == []

    def test_a_long_title_is_w091(self, tmp_path: Path) -> None:
        page_file = _metadata_page(tmp_path, f'{{"title": "{"t" * 61}"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_titles()
        assert _ids(messages) == ["next.W091"]
        assert "61 characters, over 60" in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_a_raised_maximum_allows_the_title(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, f'{{"title": "{"t" * 61}"}}')
        with (
            override_settings(NEXT_FRAMEWORK=_scope(CHECKS={"TITLE_MAX": 70})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            assert check_seo_titles() == []


class TestSeoAuditsSkipDynamicPages:
    """The title and description audits leave a page a callable rewrites alone."""

    def _tree(self, tmp_path: Path) -> Path:
        _metadata_page(tmp_path, '{"title": "Site"}')
        return _metadata_page(tmp_path / "static", '{"title": "Site"}')

    def test_a_page_with_its_own_callable_is_skipped(self, tmp_path: Path) -> None:
        sibling = self._tree(tmp_path)
        _write_page(tmp_path / "dynamic", OWN_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            titles = check_seo_titles()
            descriptions = check_seo_description()
        assert _ids(titles) == ["next.W090"]
        assert "'/dynamic'" not in titles[0].msg
        assert sorted(m.obj for m in descriptions) == sorted(
            [str(tmp_path / "page.py"), str(sibling)]
        )

    def test_a_page_under_an_inherited_callable_is_skipped(
        self, tmp_path: Path
    ) -> None:
        self._tree(tmp_path)
        _write_page(tmp_path / "posts", INHERITED_CALLABLE)
        _metadata_page(tmp_path / "posts" / "one", '{"title": "Site"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            titles = check_seo_titles()
            descriptions = check_seo_description()
        assert _ids(titles) == ["next.W090"]
        assert "'/posts" not in titles[0].msg
        assert "'/'" in titles[0].msg
        assert "'/static'" in titles[0].msg
        assert all("posts" not in str(m.obj) for m in descriptions)

    def test_a_page_beside_an_uninherited_callable_is_still_audited(
        self, tmp_path: Path
    ) -> None:
        self._tree(tmp_path)
        _write_page(tmp_path / "posts", OWN_CALLABLE)
        child = _metadata_page(tmp_path / "posts" / "one", '{"title": "Site"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            titles = check_seo_titles()
        assert _ids(titles) == ["next.W090"]
        assert "'/posts/one'" in titles[0].msg
        assert str(child) in [str(tmp_path / "page.py"), str(child)]

    def test_the_canonical_and_alternates_audits_still_read_a_dynamic_page(
        self, tmp_path: Path
    ) -> None:
        _write_page(
            tmp_path / "dynamic",
            'metadata = {"canonical": "/nowhere/", '
            '"alternates": {"languages": {"en": "/en/"}}}\n',
        )
        _write_page(tmp_path / "dynamic" / "leaf", OWN_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            canonical = check_seo_canonical()
            alternates = check_seo_alternates()
        assert _ids(canonical) == ["next.W093", "next.W093"]
        assert _ids(alternates) == ["next.W095", "next.W095"]


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
        page_file = _metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_canonical()
        assert _ids(messages) == ["next.W093"]
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
        _metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_seo_canonical() == []

    def test_a_literal_canonical_on_a_dynamic_route_is_w094(
        self, tmp_path: Path
    ) -> None:
        page_file = _metadata_page(
            tmp_path / "blog" / "[slug]", '{"canonical": "/nowhere/"}'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_canonical()
        assert _ids(messages) == ["next.W094"]
        assert "'/blog/[slug]'" in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_a_self_canonical_on_a_dynamic_route_is_silent(
        self, tmp_path: Path
    ) -> None:
        _metadata_page(tmp_path / "blog" / "[slug]", '{"canonical": True}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_seo_canonical() == []


class TestSeoAlternates:
    """`check_seo_alternates` audits an hreflang mapping."""

    def test_a_mapping_without_x_default_is_w095(self, tmp_path: Path) -> None:
        page_file = _metadata_page(
            tmp_path, '{"alternates": {"languages": {"en": "/en/"}}}'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_alternates()
        assert _ids(messages) == ["next.W095"]
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
        _metadata_page(tmp_path, metadata)
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_seo_alternates() == []

    @override_settings(**I18N)
    def test_a_code_outside_languages_is_w096(self, tmp_path: Path) -> None:
        page_file = _metadata_page(
            tmp_path,
            '{"alternates": {"languages": {"fr": "/fr/", "en": "/en/", '
            '"x-default": "/"}}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_seo_alternates()
        assert _ids(messages) == ["next.W096"]
        assert "'fr'" in messages[0].msg
        assert "'en'" not in messages[0].msg
        assert messages[0].obj == str(page_file)

    @override_settings(**I18N)
    def test_listed_codes_are_silent(self, tmp_path: Path) -> None:
        _metadata_page(
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
        _metadata_page(pages / "hello", '{"title": "Hello"}')
        register_all()
        with override_settings(NEXT_FRAMEWORK=_framework(pages)):
            plain = check_registry.run_checks(tags=[SEO])
            deploy = check_registry.run_checks(
                tags=[SEO], include_deployment_checks=True
            )
        assert plain == []
        assert "next.W089" in _ids(deploy)

    def test_the_next_tag_alone_skips_the_audits(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        _metadata_page(pages / "hello", '{"title": "Hello"}')
        register_all()
        with override_settings(NEXT_FRAMEWORK=_framework(pages)):
            messages = check_registry.run_checks(tags=[NEXT])
        assert "next.W089" not in _ids(messages)


class TestLoadedMetadataPages:
    """`loaded_metadata_pages` folds each routed page and carries its own segment."""

    def test_a_broken_chain_folds_to_nothing(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, '{"title": "Home"}')
        with (
            override_settings(NEXT_FRAMEWORK=_scope(DEFAULTS={"title": "Acme"})),
            patch_checks_router_manager(pages_directory=tmp_path),
        ):
            init_errors, pages = loaded_metadata_pages()
        assert init_errors == []
        assert [entry.static for entry in pages] == [None]
        assert [entry.declared for entry in pages] == [False]
        assert pages[0].segment is not None

    def test_a_callable_marks_the_page_dynamic(self, tmp_path: Path) -> None:
        _write_page(tmp_path, INHERITED_CALLABLE)
        _metadata_page(tmp_path / "leaf", '{"title": "Leaf"}')
        _write_page(tmp_path / "named", NAMED_CALLABLE)
        with patch_checks_router_manager(pages_directory=tmp_path):
            _init_errors, pages = loaded_metadata_pages()
        by_trail = {entry.url_path: entry for entry in pages}
        assert by_trail[""].dynamic is True
        assert by_trail["leaf"].dynamic is True
        assert by_trail["named"].dynamic is True
        assert by_trail["named"].raw is None
        assert by_trail["leaf"].static is not None

    def test_a_folded_page_reports_its_chain(self, tmp_path: Path) -> None:
        _metadata_page(tmp_path, '{"title": "Root"}')
        _write_page(tmp_path / "child", "x = 1\n")
        with patch_checks_router_manager(pages_directory=tmp_path):
            _init_errors, pages = loaded_metadata_pages()
        by_trail = {entry.url_path: entry for entry in pages}
        assert by_trail["child"].declared is True
        assert by_trail["child"].segment is None
        assert by_trail["child"].static is not None
        assert str(by_trail["child"].static.title) == "Root"
