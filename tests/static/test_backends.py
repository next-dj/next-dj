from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
from django.test import override_settings

from next.static import (
    StaticBackend,
    StaticFilesBackend,
    StaticsFactory,
)
from next.static.backends import StaticBackend as _StaticBackendDirect
from next.static.signals import backend_loaded


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest


CSS_URL = "https://cdn.example.com/site.css"
JS_URL = "https://cdn.example.com/site.js"
MJS_URL = "https://cdn.example.com/site.mjs"


class _CollectingBackend(StaticBackend):
    """Minimal concrete backend for ABC compliance + config probing."""

    def register_file(
        self,
        source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        return f"/{logical_name}.{kind}"

    def render_link_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,
    ) -> str:
        del request
        return f"<link {url}>"

    def render_script_tag(
        self,
        url: str,
        *,
        request: HttpRequest | None = None,
    ) -> str:
        del request
        return f"<script {url}>"


class TestStaticBackendContract:
    """StaticBackend ABC enforces a uniform init signature."""

    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            StaticBackend()  # type: ignore[abstract]

    def test_accepts_config_mapping(self) -> None:
        config = {"BACKEND": "x", "OPTIONS": {"k": 1}}
        backend = _CollectingBackend(config)
        assert backend.config == config

    def test_config_defaults_to_empty(self) -> None:
        backend = _CollectingBackend()
        assert dict(backend.config) == {}


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
            {
                "OPTIONS": {
                    "css_tag": '<link rel="stylesheet" crossorigin href="{url}">',
                },
            }
        )
        assert backend.render_link_tag(CSS_URL) == (
            f'<link rel="stylesheet" crossorigin href="{CSS_URL}">'
        )

    def test_custom_js_tag_with_defer(self) -> None:
        backend = StaticFilesBackend(
            {
                "OPTIONS": {
                    "js_tag": '<script defer src="{url}"></script>',
                },
            }
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

    def test_default_backend_ignores_request(self) -> None:
        backend = StaticFilesBackend()
        sentinel = object()
        assert backend.render_link_tag(CSS_URL, request=sentinel) == (  # type: ignore[arg-type]
            f'<link rel="stylesheet" href="{CSS_URL}">'
        )
        assert backend.render_script_tag(JS_URL, request=sentinel) == (  # type: ignore[arg-type]
            f'<script src="{JS_URL}"></script>'
        )
        assert backend.render_module_tag(MJS_URL, request=sentinel) == (  # type: ignore[arg-type]
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


class TestStaticsFactoryCreateBackend:
    """StaticsFactory.create_backend instantiates backends from dict config."""

    def test_uses_default_backend_when_missing(self) -> None:
        backend = StaticsFactory.create_backend({})
        assert isinstance(backend, StaticFilesBackend)

    def test_instantiates_named_backend(self) -> None:
        backend = StaticsFactory.create_backend(
            {"BACKEND": "next.static.StaticFilesBackend"}
        )
        assert isinstance(backend, StaticFilesBackend)

    def test_passes_full_config_to_backend(self) -> None:
        config: Mapping[str, Any] = {
            "BACKEND": "next.static.StaticFilesBackend",
            "OPTIONS": {"css_tag": '<link href="{url}">'},
        }
        backend = StaticsFactory.create_backend(config)
        assert backend.config == config
        assert backend.render_link_tag("x") == '<link href="x">'

    def test_rejects_non_subclass(self) -> None:
        with pytest.raises(TypeError, match="not a StaticBackend subclass"):
            StaticsFactory.create_backend({"BACKEND": "builtins.dict"})

    def test_rejects_missing_import_path(self) -> None:
        with pytest.raises(ImportError):
            StaticsFactory.create_backend(
                {"BACKEND": "next.static.does_not_exist.Backend"}
            )

    def test_fires_backend_loaded_signal(self) -> None:
        received: list[dict[str, Any]] = []

        def _listener(sender: object, **kwargs: object) -> None:
            received.append({"sender": sender, **kwargs})

        backend_loaded.connect(_listener)
        try:
            backend = StaticsFactory.create_backend(
                {"BACKEND": "next.static.StaticFilesBackend", "OPTIONS": {}}
            )
        finally:
            backend_loaded.disconnect(_listener)

        assert len(received) == 1
        assert received[0]["sender"] is StaticFilesBackend
        assert received[0]["instance"] is backend
        assert received[0]["config"] == {
            "BACKEND": "next.static.StaticFilesBackend",
            "OPTIONS": {},
        }


class TestStaticBackendReexport:
    """Public re-export from next.static matches the direct import."""

    def test_same_object(self) -> None:
        assert StaticBackend is _StaticBackendDirect


class TestStaticsFactoryWithSettingsOverride:
    """Factory respects NEXT_FRAMEWORK['DEFAULT_STATIC_BACKENDS'] at construction."""

    def test_build_from_settings_config(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {"css_tag": '<link integrity href="{url}">'},
                    }
                ]
            }
        ):
            backend = StaticsFactory.create_backend(
                {
                    "BACKEND": "next.static.StaticFilesBackend",
                    "OPTIONS": {"css_tag": '<link integrity href="{url}">'},
                }
            )
        assert backend.render_link_tag("x") == '<link integrity href="x">'
