from pathlib import Path

import pytest
from django.test import override_settings

from next.pages.checks import (
    check_metadata_head_literals,
    check_metadata_head_tags,
    check_metadata_social_folds,
)
from next.pages.checks.metadata.head import viewport_pairs
from next.pages.metadata import Viewport
from next.pages.metadata.scope import SITE_SOURCE
from tests.pages.checks.metadata.trees import (
    framework,
    metadata_page,
    scope,
    templated_page,
)
from tests.support import I18N, check_ids, patch_checks_router_manager


def _messages(tmp_path: Path, metadata: str) -> list[tuple[str, str]]:
    metadata_page(tmp_path, metadata)
    with patch_checks_router_manager(pages_directory=tmp_path):
        return [(message.id, message.msg) for message in check_metadata_head_tags()]


class TestLinks:
    """A free-form link names a rel of its own, an origin, and what it preloads."""

    def test_a_well_formed_link_is_silent(self, tmp_path: Path) -> None:
        assert (
            _messages(
                tmp_path,
                '{"links": [{"rel": "preconnect", "href": "https://fonts.example"}, '
                '{"rel": "preload", "href": "/f.woff2", "as": "font"}, '
                '{"rel": "author", "href": "/humans.txt"}]}',
            )
            == []
        )

    @pytest.mark.parametrize(
        ("rel", "fragment"),
        [
            ("canonical", "the canonical key"),
            ("alternate", "alternates.feeds"),
            ("icon", "through icons"),
            ("shortcut icon", "through icons"),
            ("apple-touch-icon", "icons.apple"),
            ("mask-icon", "icons.other"),
            ("manifest", "the manifest key"),
            ("stylesheet", "{% use_style %}"),
        ],
    )
    def test_a_rel_a_key_renders_is_e122(
        self, tmp_path: Path, rel: str, fragment: str
    ) -> None:
        messages = _messages(
            tmp_path, f'{{"links": [{{"rel": "{rel}", "href": "/x"}}]}}'
        )
        assert [code for code, _msg in messages].count("next.E122") >= 1
        assert fragment in messages[0][1]

    @pytest.mark.parametrize(
        "href",
        [
            "https://fonts.example/css",
            "/local",
            "https://a.example/?q=1",
            "https://a.example/#x",
        ],
        ids=["path", "relative", "query", "fragment"],
    )
    def test_a_preconnect_to_no_origin_is_e123(self, tmp_path: Path, href: str) -> None:
        messages = _messages(
            tmp_path, f'{{"links": [{{"rel": "dns-prefetch", "href": "{href}"}}]}}'
        )
        assert messages[0][0] == "next.E123"
        assert "is an origin" in messages[0][1]

    def test_a_lazy_preconnect_href_is_not_forced(self, tmp_path: Path) -> None:
        templated_page(
            tmp_path,
            "from django.utils.functional import lazy\n\n"
            'metadata = {"links": [{"rel": "preconnect", '
            '"href": lazy(str, str)("ftp://x/a")}]}\n',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_head_tags() == []

    def test_a_preload_without_as_is_e123(self, tmp_path: Path) -> None:
        messages = _messages(tmp_path, '{"links": [{"rel": "preload", "href": "/f"}]}')
        assert messages == [
            (
                "next.E123",
                (
                    f"{tmp_path / 'page.py'} declares links[0] as a preload without "
                    "'as'. Name what it preloads, the browser ignores it otherwise."
                ),
            )
        ]

    def test_an_unknown_rel_is_w110(self, tmp_path: Path) -> None:
        messages = _messages(
            tmp_path, '{"links": [{"rel": "prefetchh", "href": "/f"}]}'
        )
        assert [code for code, _msg in messages] == ["next.W105"]
        assert "'prefetchh'" in messages[0][1]


class TestIcons:
    """An icon spells its sizes and type, a mask icon its color, each one once."""

    def test_well_formed_icons_are_silent(self, tmp_path: Path) -> None:
        metadata = (
            '{"icons": {"icon": [{"url": "/a.png", "sizes": "16x16 32X32"}, '
            '{"url": "/a.svg", "sizes": "any", "type": "image/svg+xml"}], '
            '"other": [{"rel": "mask-icon", "url": "/m.svg", "color": "#000"}]}}'
        )
        assert _messages(tmp_path, metadata) == []

    @pytest.mark.parametrize(
        ("icon", "fragment"),
        [
            ('{"url": "/a.png", "sizes": "big"}', "the sizes 'big'"),
            ('{"url": "/a.png", "type": "text/html"}', "the type 'text/html'"),
        ],
        ids=["sizes", "type"],
    )
    def test_a_malformed_icon_is_e124(
        self, tmp_path: Path, icon: str, fragment: str
    ) -> None:
        messages = _messages(tmp_path, f'{{"icons": {{"icon": {icon}}}}}')
        assert [code for code, _msg in messages] == ["next.E124"]
        assert fragment in messages[0][1]

    def test_a_mask_icon_without_a_color_is_e124(self, tmp_path: Path) -> None:
        messages = _messages(
            tmp_path, '{"icons": {"other": [{"rel": "mask-icon", "url": "/m.svg"}]}}'
        )
        assert [code for code, _msg in messages] == ["next.E124"]
        assert "without a color" in messages[0][1]

    def test_a_repeated_icon_is_w111(self, tmp_path: Path) -> None:
        messages = _messages(tmp_path, '{"icons": {"icon": ["/a.png", "/b.png"]}}')
        assert [code for code, _msg in messages] == ["next.W106"]


class TestNames:
    """A free-form name a typed key renders is refused."""

    @pytest.mark.parametrize(
        ("metadata", "key"),
        [
            ('{"other": {"Keywords": "a"}}', "keywords"),
            ('{"other": {"theme-color": "#fff"}}', "theme_color"),
            ('{"other": {"p:domain_verify": "x"}}', "verification.pinterest"),
            ('{"other": {"twitter:card": "summary"}}', "twitter"),
            ('{"properties": {"og:title": "T"}}', "through og,"),
            ('{"properties": {"book:isbn": "978"}}', "og.book"),
        ],
        ids=["keywords", "theme_color", "pinterest", "twitter", "og", "book"],
    )
    def test_a_typed_name_is_e125(
        self, tmp_path: Path, metadata: str, key: str
    ) -> None:
        messages = _messages(tmp_path, metadata)
        assert [code for code, _msg in messages] == ["next.E125"]
        assert key in messages[0][1]

    @pytest.mark.parametrize(
        "metadata",
        [
            '{"other": {"application-name": "Acme", "og:title": "x"}}',
            '{"properties": {"fb:app_id": "1", "twitter:card": "x"}}',
        ],
        ids=["other", "properties"],
    )
    def test_a_free_name_is_silent(self, tmp_path: Path, metadata: str) -> None:
        assert _messages(tmp_path, metadata) == []


class TestViewport:
    """A viewport keeps its scales in range and the page zoomable."""

    @pytest.mark.parametrize(
        "viewport",
        [
            '"width=device-width, initial-scale=1"',
            '{"width": "device-width", "initial_scale": 1, "maximum_scale": 5}',
        ],
        ids=["string", "mapping"],
    )
    def test_a_zoomable_viewport_is_silent(self, tmp_path: Path, viewport: str) -> None:
        assert _messages(tmp_path, f'{{"viewport": {viewport}}}') == []

    @pytest.mark.parametrize(
        "viewport",
        ['"initial-scale=20"', '"minimum-scale=0.01"', '"initial-scale=big"'],
        ids=["above", "below", "not_a_number"],
    )
    def test_a_scale_out_of_range_is_e126(self, tmp_path: Path, viewport: str) -> None:
        messages = _messages(tmp_path, f'{{"viewport": {viewport}}}')
        assert [code for code, _msg in messages] == ["next.E126"]

    @pytest.mark.parametrize(
        "viewport",
        ['{"user_scalable": False}', '"maximum-scale=1"', '"user-scalable=0"'],
        ids=["mapping", "maximum", "zero"],
    )
    def test_a_viewport_without_zoom_is_w112(
        self, tmp_path: Path, viewport: str
    ) -> None:
        messages = _messages(tmp_path, f'{{"viewport": {viewport}}}')
        assert [code for code, _msg in messages] == ["next.W107"]

    def test_the_pairs_of_either_form(self) -> None:
        assert viewport_pairs(None) == {}
        assert viewport_pairs("Width = device-width; bare") == {"width": "device-width"}
        assert viewport_pairs(Viewport(user_scalable=False)) == {"user-scalable": "no"}


class TestSettingsTier:
    """The settings tier is checked like a page."""

    def test_the_defaults_are_checked(self) -> None:
        defaults = {"links": [{"rel": "icon", "href": "/i.png"}]}
        with override_settings(NEXT_FRAMEWORK=scope(DEFAULTS=defaults)):
            messages = check_metadata_head_tags()
        assert check_ids(messages) == ["next.E122"]
        assert messages[0].msg.startswith(SITE_SOURCE)


class TestSocialFolds:
    """An og locale is of the `ll_CC` form, or derivable from the language."""

    def test_a_derivable_language_is_silent(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"og": {"type": "website"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_social_folds() == []

    def test_a_declared_locale_of_another_form_is_w113(self, tmp_path: Path) -> None:
        metadata_page(
            tmp_path,
            '{"og": {"locale": "en-US", "locale_alternates": ["de_DE", "fr"]}}',
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_social_folds()
        assert check_ids(messages) == ["next.W108", "next.W108"]
        assert "'en-US'" in messages[0].msg
        assert "'fr'" in messages[1].msg

    def test_a_locale_facebook_spells_otherwise_is_w113(self, tmp_path: Path) -> None:
        metadata_page(
            tmp_path, '{"og": {"locale": "ar_AA", "locale_alternates": ["no_NO"]}}'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_social_folds()
        assert check_ids(messages) == ["next.W108", "next.W108"]
        assert "Facebook reads ar_AR" in messages[0].msg
        assert "Facebook reads nb_NO" in messages[1].msg

    @override_settings(
        LANGUAGE_CODE="kab",
        LANGUAGES=[("kab", "Kabyle"), ("en", "English"), ("ast", "Asturian")],
    )
    def test_an_underived_language_is_one_w113(self, tmp_path: Path) -> None:
        metadata_page(tmp_path / "a", '{"og": {"locale_alternates": True}}')
        metadata_page(tmp_path / "b", '{"og": {"type": "website"}}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_metadata_social_folds()
        assert check_ids(messages) == ["next.W108"]
        assert "'ast', 'kab'" in messages[0].msg

    @override_settings(**I18N)
    def test_a_page_without_og_is_silent(self, tmp_path: Path) -> None:
        metadata_page(tmp_path, '{"title": "T"}')
        with patch_checks_router_manager(pages_directory=tmp_path):
            assert check_metadata_social_folds() == []


class TestLiterals:
    """A template writing the viewport or theme color a fold declares warns."""

    def _project(self, tmp_path: Path, layout: str, metadata: str) -> Path:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text(layout)
        metadata_page(pages / "hello", metadata)
        return pages

    def test_a_literal_in_a_component_is_w118(self, tmp_path: Path) -> None:
        pages = self._project(
            tmp_path,
            '<html>{% component "head" %}{% template %}</html>',
            '{"viewport": "width=device-width", "theme_color": "#fff"}',
        )
        folder = pages / "_components" / "head"
        folder.mkdir(parents=True)
        (folder / "component.djx").write_text(
            "<head><META content='x' name='viewport'>{% metadata %}</head>"
        )
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            messages = check_metadata_head_literals()
        assert check_ids(messages) == ["next.W109"]
        assert '<meta name="viewport">' in messages[0].msg

    def test_a_theme_color_literal_is_w118(self, tmp_path: Path) -> None:
        pages = self._project(
            tmp_path,
            '<html><meta name="theme-color" content="#000">{% template %}</html>',
            '{"theme_color": "#fff"}',
        )
        with patch_checks_router_manager(pages_directory=pages):
            messages = check_metadata_head_literals()
        assert check_ids(messages) == ["next.W109"]
        assert "'theme_color'" in messages[0].msg

    @pytest.mark.parametrize(
        ("layout", "metadata"),
        [
            (
                '<html><meta name="viewport" content="x">{% template %}</html>',
                '{"title": "T"}',
            ),
            ("<html>{% metadata %}{% template %}</html>", '{"viewport": "x"}'),
        ],
        ids=["undeclared", "no_literal"],
    )
    def test_no_overlap_is_silent(
        self, tmp_path: Path, layout: str, metadata: str
    ) -> None:
        pages = self._project(tmp_path, layout, metadata)
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_head_literals() == []

    def test_a_render_page_is_silent(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text('<meta name="viewport">{% template %}')
        templated_page(
            pages,
            'metadata = {"viewport": "x"}\n\ndef render(request):\n    return "x"\n',
        )
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_head_literals() == []
