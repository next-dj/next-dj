from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.contrib.staticfiles.storage import staticfiles_storage
from django.test import override_settings

import next.static
from next.static import (
    StaticAssetNotFoundError,
    StaticAssetTraversalError,
    StaticBackend,
    StaticFilesBackend,
    static_name,
)
from next.static.backends import StaticBackend as _StaticBackendDirect
from tests.support import static_names_resolved_by


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest


STORAGE = {"next/a.css": "/static/next/a.css", "a.css": "/static/a.css"}
REBUILT = {"next/a.css": "/static/next/a.9f1.css", "a.css": "/static/a.4b2.css"}

CSS_URL = "https://cdn.example.com/site.css"
JS_URL = "https://cdn.example.com/site.js"
MJS_URL = "https://cdn.example.com/site.mjs"
BREAKOUT_URL = '/static/a.css"><script>alert(1)</script>'


class _CollectingBackend(StaticBackend):
    """Minimal concrete backend for ABC compliance + config probing."""

    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        return f"/{logical_name}.{kind}"

    def render_link_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        del request
        return f"<link {url}>"

    def render_script_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        del request
        return f"<script {url}>"


class TestStaticBackendContract:
    """StaticBackend ABC enforces a uniform init signature."""

    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            StaticBackend()  # type: ignore[abstract]

    def test_accepts_config_mapping(self) -> None:
        config = {"BACKEND": "x", "OPTIONS": {"k": 1}}
        backend = _CollectingBackend(config)
        assert backend.config == config

    def test_config_defaults_to_empty(self) -> None:
        backend = _CollectingBackend()
        assert dict(backend.config) == {}


class TestAssetUrlHook:
    """`asset_url` is the request-aware URL seam and defaults to identity."""

    def test_base_class_returns_the_url_unchanged(self) -> None:
        backend = _CollectingBackend()
        assert backend.asset_url(CSS_URL) == CSS_URL

    def test_base_class_ignores_the_request(self, mock_http_request) -> None:
        backend = _CollectingBackend()
        assert backend.asset_url(CSS_URL, request=mock_http_request()) == CSS_URL

    def test_default_backend_returns_the_url_unchanged(self, mock_http_request) -> None:
        backend = StaticFilesBackend()
        assert backend.asset_url(JS_URL, request=mock_http_request()) == JS_URL


class TestStaticFilesBackendDefaults:
    """Default tag templates mirror Django conventions."""

    def test_defaults_without_options(self) -> None:
        backend = StaticFilesBackend()
        assert backend.render_link_tag(CSS_URL) == (
            f'<link rel="stylesheet" href="{CSS_URL}">'
        )
        assert backend.render_script_tag(JS_URL) == (
            f'<script src="{JS_URL}"></script>'
        )
        assert backend.render_module_tag(MJS_URL) == (
            f'<script type="module" src="{MJS_URL}"></script>'
        )


class TestStaticFilesBackendOptions:
    """OPTIONS.css_tag/js_tag override default templates."""

    def test_custom_css_tag_with_crossorigin(self) -> None:
        backend = StaticFilesBackend(
            {"OPTIONS": {"css_tag": '<link rel="stylesheet" crossorigin href="{url}">'}}
        )
        assert backend.render_link_tag(CSS_URL) == (
            f'<link rel="stylesheet" crossorigin href="{CSS_URL}">'
        )

    def test_custom_js_tag_with_defer(self) -> None:
        backend = StaticFilesBackend(
            {"OPTIONS": {"js_tag": '<script defer src="{url}"></script>'}}
        )
        assert backend.render_script_tag(JS_URL) == (
            f'<script defer src="{JS_URL}"></script>'
        )

    def test_options_none_falls_back_to_defaults(self) -> None:
        backend = StaticFilesBackend({"OPTIONS": None})
        assert backend.render_link_tag(CSS_URL) == (
            f'<link rel="stylesheet" href="{CSS_URL}">'
        )

    def test_empty_options_mapping(self) -> None:
        backend = StaticFilesBackend({"OPTIONS": {}})
        assert backend.render_script_tag(JS_URL) == (
            f'<script src="{JS_URL}"></script>'
        )

    def test_custom_module_tag(self) -> None:
        backend = StaticFilesBackend(
            {
                "OPTIONS": {
                    "module_tag": '<script type="module" defer src="{url}"></script>'
                }
            }
        )
        assert backend.render_module_tag(MJS_URL) == (
            f'<script type="module" defer src="{MJS_URL}"></script>'
        )

    def test_options_none_falls_back_to_module_default(self) -> None:
        backend = StaticFilesBackend({"OPTIONS": None})
        assert backend.render_module_tag(MJS_URL) == (
            f'<script type="module" src="{MJS_URL}"></script>'
        )

    def test_default_backend_ignores_request(self, mock_http_request) -> None:
        backend = StaticFilesBackend()
        request = mock_http_request()
        assert backend.render_link_tag(CSS_URL, request=request) == (
            f'<link rel="stylesheet" href="{CSS_URL}">'
        )
        assert backend.render_script_tag(JS_URL, request=request) == (
            f'<script src="{JS_URL}"></script>'
        )
        assert backend.render_module_tag(MJS_URL, request=request) == (
            f'<script type="module" src="{MJS_URL}"></script>'
        )


class TestStaticFilesBackendRegisterFile:
    """register_file resolves URLs through staticfiles storage."""

    def test_uses_next_namespace(self, tmp_path: Path) -> None:
        backend = StaticFilesBackend()
        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/foo.css",
        ) as url:
            resolved = backend.register_file(tmp_path / "foo.css", "foo", "css")
        assert resolved == "/static/next/foo.css"
        url.assert_called_once_with("next/foo.css")

    def test_uses_kind_extension(self, tmp_path: Path) -> None:
        backend = StaticFilesBackend()
        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/foo.js",
        ) as url:
            backend.register_file(tmp_path / "foo.js", "foo", "js")
        url.assert_called_once_with("next/foo.js")

    def test_caches_repeated_lookups(self, tmp_path: Path) -> None:
        backend = StaticFilesBackend()
        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/a.css",
        ) as url:
            backend.register_file(tmp_path / "a.css", "a", "css")
            backend.register_file(tmp_path / "a.css", "a", "css")
        assert url.call_count == 1

    def test_raises_runtime_error_on_manifest_miss(self, tmp_path: Path) -> None:
        backend = StaticFilesBackend()
        with (
            mock.patch(
                "next.static.backends.staticfiles_storage.url",
                side_effect=ValueError("not in manifest"),
            ),
            pytest.raises(RuntimeError, match="missing from Django staticfiles"),
        ):
            backend.register_file(tmp_path / "x.css", "x", "css")


class TestUrlMemoInvalidation:
    """The memoised URL lives only as long as the manifest that answered it."""

    def _register(self, backend: StaticFilesBackend, tmp_path: Path, url: str) -> str:
        with mock.patch(
            "next.static.backends.staticfiles_storage.url", return_value=url
        ):
            return backend.register_file(tmp_path / "a.css", "a", "css")

    def test_forget_urls_sends_the_next_lookup_back_to_the_manifest(
        self, tmp_path: Path
    ) -> None:
        backend = StaticFilesBackend()
        first = self._register(backend, tmp_path, "/static/next/a.css")
        backend.forget_urls()
        second = self._register(backend, tmp_path, "/static/next/a.9f1.css")
        assert (first, second) == ("/static/next/a.css", "/static/next/a.9f1.css")

    def test_the_hook_reaches_a_backend_of_any_other_shape(self) -> None:
        """The memo and the hook that drops it both sit on the base contract."""
        backend = _CollectingBackend()
        backend._url_cache[("a", ".css")] = "/static/next/a.css"

        backend.forget_urls()

        assert not backend._url_cache


class TestResolveUrl:
    """`resolve_url` turns an authored reference into the URL a document prints."""

    def test_the_base_contract_keeps_the_reference_literal(self) -> None:
        backend = _CollectingBackend()
        assert backend.resolve_url("css/theme.css") == "css/theme.css"

    def test_a_name_resolves_through_staticfiles(self) -> None:
        backend = StaticFilesBackend()
        with static_names_resolved_by(
            {"css/theme.css": "/static/css/theme.css"}
        ) as url:
            resolved = backend.resolve_url("css/theme.css")
        assert resolved == "/static/css/theme.css"
        url.assert_called_once_with("css/theme.css")

    def test_a_ready_url_never_reaches_storage(self) -> None:
        backend = StaticFilesBackend()
        with static_names_resolved_by({}) as url:
            assert backend.resolve_url(CSS_URL) == CSS_URL
        assert url.call_count == 0

    def test_a_repeated_name_is_answered_from_the_memo(self) -> None:
        backend = StaticFilesBackend()
        with static_names_resolved_by({"a.css": "/static/a.css"}) as url:
            first = backend.resolve_url("a.css")
            second = backend.resolve_url("a.css")
        assert (first, second) == ("/static/a.css", "/static/a.css")
        assert url.call_count == 1

    def test_a_manifest_miss_raises_with_the_reference_on_it(self) -> None:
        backend = StaticFilesBackend()
        with (
            static_names_resolved_by({}),
            pytest.raises(StaticAssetNotFoundError) as excinfo,
        ):
            backend.resolve_url("css/typo.css")
        assert excinfo.value.path == "css/typo.css"

    def test_storage_is_asked_for_the_normalised_name(self) -> None:
        backend = StaticFilesBackend()
        with static_names_resolved_by(
            {"css/theme.css": "/static/css/theme.css"}
        ) as url:
            resolved = backend.resolve_url("./css/x/../theme.css")
        assert resolved == "/static/css/theme.css"
        url.assert_called_once_with("css/theme.css")

    def test_a_miss_names_the_normalised_name_rather_than_the_reference(self) -> None:
        backend = StaticFilesBackend()
        with (
            static_names_resolved_by({}),
            pytest.raises(StaticAssetNotFoundError) as excinfo,
        ):
            backend.resolve_url("css/./typo.css")
        assert excinfo.value.path == "css/typo.css"

    def test_a_reference_climbing_above_the_root_is_refused(self) -> None:
        backend = StaticFilesBackend()
        with (
            static_names_resolved_by({}) as url,
            pytest.raises(StaticAssetTraversalError),
        ):
            backend.resolve_url("../../etc/passwd.css")
        assert url.call_count == 0


class TestResolveUrlMemoIsReadFirst:
    """The memo answers before the reference is classified a second time."""

    def test_a_ready_url_is_held_as_a_pass_through(self) -> None:
        backend = StaticFilesBackend()
        with static_names_resolved_by({}):
            backend.resolve_url(CSS_URL)
        assert backend._url_cache[("name", CSS_URL)] == CSS_URL

    def test_a_repeated_ready_url_is_classified_only_once(self) -> None:
        backend = StaticFilesBackend()
        with mock.patch(
            "next.static.backends.static_name", side_effect=static_name
        ) as classify:
            first = backend.resolve_url(CSS_URL)
            second = backend.resolve_url(CSS_URL)
        assert (first, second) == (CSS_URL, CSS_URL)
        classify.assert_called_once_with(CSS_URL)

    def test_forget_urls_drops_the_pass_through_with_the_names(self) -> None:
        backend = StaticFilesBackend()
        with static_names_resolved_by(STORAGE):
            backend.resolve_url("a.css")
            backend.resolve_url(CSS_URL)

        backend.forget_urls()

        assert not backend._url_cache
        with mock.patch(
            "next.static.backends.static_name", side_effect=static_name
        ) as classify:
            backend.resolve_url(CSS_URL)
        classify.assert_called_once_with(CSS_URL)


class TestManifestMissReadsAlikeOnBothPaths:
    """A co-located file and an authored name fail with one error and one message."""

    def test_both_paths_raise_the_same_error_class(self, tmp_path: Path) -> None:
        backend = StaticFilesBackend()
        with static_names_resolved_by({}):
            with pytest.raises(StaticAssetNotFoundError) as colocated:
                backend.register_file(tmp_path / "x.css", "x", "css")
            with pytest.raises(StaticAssetNotFoundError) as named:
                backend.resolve_url("next/x.css")
        assert str(colocated.value) == str(named.value)
        assert colocated.value.path == named.value.path == "next/x.css"


class TestUrlMemoKeySpaces:
    """One memo holds both resolvers, so their keys can never answer for each other."""

    def test_a_file_and_a_name_keep_their_own_answers(self, tmp_path: Path) -> None:
        backend = StaticFilesBackend()
        with static_names_resolved_by(STORAGE):
            file_url = backend.register_file(tmp_path / "a.css", "a", "css")
            name_url = backend.resolve_url("a.css")
        assert (file_url, name_url) == ("/static/next/a.css", "/static/a.css")

    def test_forget_urls_clears_both_spaces_in_one_call(self, tmp_path: Path) -> None:
        backend = StaticFilesBackend()
        with static_names_resolved_by(STORAGE):
            backend.register_file(tmp_path / "a.css", "a", "css")
            backend.resolve_url("a.css")

        backend.forget_urls()

        with static_names_resolved_by(REBUILT) as url:
            file_url = backend.register_file(tmp_path / "a.css", "a", "css")
            name_url = backend.resolve_url("a.css")
        assert (file_url, name_url) == ("/static/next/a.9f1.css", "/static/a.4b2.css")
        assert url.call_count == 2


class TestStaticNamesResolvedBy:
    """The storage stand-in outlives a settings override nested inside its block.

    Patched on the class, because `STATIC_URL` rebuilds the lazy storage handle and
    an instance patch would vanish with the instance it was installed on.
    """

    def test_an_override_inside_the_block_keeps_the_mapping(self) -> None:
        with static_names_resolved_by({"a.css": "/static/a.css"}) as url:
            before = StaticFilesBackend().resolve_url("a.css")
            with override_settings(STATIC_URL="/assets/"):
                during = StaticFilesBackend().resolve_url("a.css")
            after = StaticFilesBackend().resolve_url("a.css")

        assert (before, during, after) == ("/static/a.css",) * 3
        assert url.call_count == 3

    def test_the_stand_in_is_gone_once_the_block_closes(self) -> None:
        with (
            static_names_resolved_by({"a.css": "/static/a.css"}),
            override_settings(STATIC_URL="/assets/"),
        ):
            pass

        assert staticfiles_storage.url("a.css") == "/static/a.css"


class TestStaticBackendReexport:
    """Public re-export from next.static matches the direct import."""

    def test_same_object(self) -> None:
        assert StaticBackend is _StaticBackendDirect

    def test_factory_is_gone_from_the_public_surface(self) -> None:
        assert not hasattr(next.static, "StaticsFactory")


class TestTagTemplatesEscapeTheUrl:
    """A tag is spliced into the page past the engine, so its URL is escaped here."""

    @pytest.mark.parametrize(
        "renderer", ["render_link_tag", "render_script_tag", "render_module_tag"]
    )
    def test_a_url_closing_the_attribute_cannot_open_an_element(self, renderer) -> None:
        backend = StaticFilesBackend()

        rendered = getattr(backend, renderer)(BREAKOUT_URL)

        assert "<script>alert(1)</script>" not in rendered
        assert "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in rendered

    @pytest.mark.parametrize(
        ("renderer", "expected"),
        [
            (
                "render_link_tag",
                '<link rel="stylesheet" href="/static/a.css?v=1&amp;x=2">',
            ),
            ("render_script_tag", '<script src="/static/a.js?v=1&amp;x=2"></script>'),
            (
                "render_module_tag",
                '<script type="module" src="/static/a.mjs?v=1&amp;x=2"></script>',
            ),
        ],
    )
    def test_a_query_ampersand_renders_as_an_entity(self, renderer, expected) -> None:
        backend = StaticFilesBackend()
        suffix = {"render_link_tag": "css", "render_script_tag": "js"}.get(
            renderer, "mjs"
        )

        assert getattr(backend, renderer)(f"/static/a.{suffix}?v=1&x=2") == expected

    def test_a_custom_tag_template_escapes_the_url_too(self) -> None:
        """The escape sits in the renderer, so a project template inherits it."""
        backend = StaticFilesBackend(
            {"OPTIONS": {"css_tag": '<link rel="stylesheet" crossorigin href="{url}">'}}
        )

        rendered = backend.render_link_tag(BREAKOUT_URL)

        assert "<script>alert(1)</script>" not in rendered
        assert rendered.startswith('<link rel="stylesheet" crossorigin href="')
