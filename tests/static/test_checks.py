from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.checks import Error, Warning as DjangoWarning
from django.core.checks.registry import registry
from django.test import override_settings

import next.static.checks as checks_module
from next.apps.staticfiles import _APP_DIRECTORIES_PATH
from next.checks import NEXT
from next.components import FileComponentsBackend
from next.static import KindRegistry, ScriptInjectionPolicy, StaticFilesBackend
from next.static.checks import (
    check_app_directories_finder,
    check_asset_kinds_are_loadable,
    check_asset_renderers,
    check_inline_asset_bodies_are_loadable,
    check_js_context_serializer,
    check_nonce_on_shared_pages,
    check_nonce_templates,
    check_reserved_js_context_keys,
    check_runtime_bundles_deployed,
    check_script_policy,
    check_static_backends,
    check_tag_templates_format,
)
from tests.support import (
    APP_FINDER_CASES,
    PROJECT_APP_DIRECTORIES_FINDER,
    AppFinderCase,
    check_ids,
    patch_checks_router_manager,
    routed,
    write_page,
)


class TestEmptyConfig:
    def test_empty_list_emits_w030(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": []}):
            messages = check_static_backends(app_configs=None)
        assert check_ids(messages) == ["next.W030"]
        assert isinstance(messages[0], DjangoWarning)

    def test_non_list_falls_back_to_defaults(self) -> None:
        """Conf coerces non-list to defaults, so checks treat it as valid."""
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": "not-a-list"}):
            messages = check_static_backends(app_configs=None)
        assert messages == []


class TestValidConfig:
    def test_single_default_backend(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [{"BACKEND": "next.static.StaticFilesBackend"}]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert messages == []

    def test_valid_options_with_placeholders(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {
                            "css_tag": '<link href="{url}">',
                            "js_tag": '<script src="{url}"></script>',
                        },
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert messages == []


class TestBadEntries:
    def test_non_dict_entry_emits_e037(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": ["not-a-dict"]}):
            messages = check_static_backends(app_configs=None)
        assert check_ids(messages) == ["next.E037"]
        assert isinstance(messages[0], Error)

    def test_non_string_backend_emits_e092(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": 123}]}):
            messages = check_static_backends(app_configs=None)
        assert check_ids(messages) == ["next.E092"]

    def test_missing_module_emits_e036(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": "does.not.exist.Backend"}]}
        ):
            messages = check_static_backends(app_configs=None)
        assert check_ids(messages) == ["next.E036"]

    def test_not_subclass_emits_e093(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": "builtins.dict"}]}
        ):
            messages = check_static_backends(app_configs=None)
        assert check_ids(messages) == ["next.E093"]

    def test_duplicate_backend_emits_e038(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {"BACKEND": "next.static.StaticFilesBackend"},
                    {"BACKEND": "next.static.StaticFilesBackend"},
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.E038" in check_ids(messages)


class TestOptionsWarnings:
    def test_css_tag_without_placeholder_emits_w031(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {"css_tag": "<link>"},
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.W031" in check_ids(messages)

    def test_js_tag_without_placeholder_emits_w031(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {"js_tag": "<script></script>"},
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.W031" in check_ids(messages)

    def test_module_tag_without_placeholder_emits_w031(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {"module_tag": '<script type="module"></script>'},
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.W031" in check_ids(messages)

    def test_non_string_tag_template_is_ignored(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": {"css_tag": 42},
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.W031" not in check_ids(messages)

    def test_options_that_are_no_mapping_name_no_tag(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "STATIC_BACKENDS": [
                    {
                        "BACKEND": "next.static.StaticFilesBackend",
                        "OPTIONS": ["css_tag", "<link>"],
                    }
                ]
            }
        ):
            messages = check_static_backends(app_configs=None)
        assert "next.W031" not in check_ids(messages)


class TestChecksRegistered:
    """System check discovery picks up every static check under the NEXT tag."""

    @pytest.mark.parametrize(
        "check",
        [
            check_static_backends,
            check_js_context_serializer,
            check_asset_kinds_are_loadable,
            check_inline_asset_bodies_are_loadable,
            check_reserved_js_context_keys,
            check_nonce_templates,
        ],
    )
    def test_registered_under_next_tag(self, check) -> None:

        assert check in registry.registered_checks
        assert NEXT in getattr(check, "tags", ())


class _NotASerializer:
    """Placeholder without a `dumps` method."""


class TestJsContextSerializerCheck:
    """check_js_context_serializer validates the configured dotted path."""

    def test_passes_when_unset(self) -> None:

        assert check_js_context_serializer() == []

    def test_passes_when_framework_setting_is_not_a_dict(self) -> None:

        with override_settings(NEXT_FRAMEWORK=["not a dict"]):
            assert check_js_context_serializer() == []

    def test_passes_for_default_json_serializer(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={
                "JS_CONTEXT_SERIALIZER": (
                    "next.static.serializers.JsonJsContextSerializer"
                )
            }
        ):
            assert check_js_context_serializer() == []

    def test_warns_on_non_string_value(self) -> None:

        with override_settings(NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": 42}):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W042"

    def test_warns_on_import_error(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": "tests.nonexistent.Missing"}
        ):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W079"
        assert "Cannot import" in messages[0].msg

    def test_warns_when_target_is_not_a_class(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={
                "JS_CONTEXT_SERIALIZER": "next.static.serializers.resolve_serializer"
            }
        ):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W080"
        assert "not a class" in messages[0].msg

    def test_warns_when_instance_fails_protocol(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={
                "JS_CONTEXT_SERIALIZER": "tests.static.test_checks._NotASerializer"
            }
        ):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W082"
        assert "JsContextSerializer protocol" in messages[0].msg

    def test_warns_when_instance_cannot_be_constructed(self) -> None:

        with override_settings(
            NEXT_FRAMEWORK={"JS_CONTEXT_SERIALIZER": "tests.static.test_checks._Boom"}
        ):
            messages = check_js_context_serializer()
        assert len(messages) == 1
        assert messages[0].id == "next.W081"
        assert "cannot be instantiated" in messages[0].msg


class _Boom:
    """Class whose constructor raises, used by the instantiation-failure test."""

    def __init__(self) -> None:
        msg = "boom"
        raise TypeError(msg)


class TestAssetKindLoadableCheck:
    """check_asset_kinds_are_loadable flags kinds the runtime cannot insert."""

    def test_builtin_kinds_are_silent(self) -> None:
        assert check_asset_kinds_are_loadable() == []

    def test_module_renderer_kind_is_silent(self, monkeypatch) -> None:
        registry_with_vue = KindRegistry()
        registry_with_vue.register(
            "vue", extension=".vue", slot="scripts", renderer="render_module_tag"
        )
        monkeypatch.setattr(checks_module, "default_kinds", registry_with_vue)
        assert check_asset_kinds_are_loadable() == []

    def test_custom_renderer_kind_emits_w074(self, monkeypatch) -> None:
        mixed = KindRegistry()
        mixed.register(
            "css", extension=".css", slot="styles", renderer="render_link_tag"
        )
        mixed.register(
            "jsx", extension=".jsx", slot="scripts", renderer="render_babel_script_tag"
        )
        monkeypatch.setattr(checks_module, "default_kinds", mixed)
        messages = check_asset_kinds_are_loadable()
        assert check_ids(messages) == ["next.W074"]
        assert isinstance(messages[0], DjangoWarning)
        assert "'jsx'" in messages[0].msg
        assert "'render_babel_script_tag'" in messages[0].msg


class OldSignatureBackend(StaticFilesBackend):
    """A backend written before renderers took the request and the nonce."""

    def render_link_tag(self, url):
        """Render the tag the old way."""
        return f'<link rel="stylesheet" href="{url}">'


class OptionsBackend(StaticFilesBackend):
    """A backend whose custom renderer takes every keyword it is given."""

    def render_babel_script_tag(self, url, **options):
        """Render a babel script tag."""
        return f'<script type="text/babel" src="{url}"></script>'


_HERE = "tests.static.test_checks"
_OLD = {"STATIC_BACKENDS": [{"BACKEND": f"{_HERE}.OldSignatureBackend"}]}


def _babel_kind() -> KindRegistry:
    kinds = KindRegistry()
    kinds.register(
        "jsx", extension=".jsx", slot="scripts", renderer="render_babel_script_tag"
    )
    return kinds


class TestAssetRenderersCheck:
    """check_asset_renderers refuses a renderer the rendering backend cannot call."""

    def test_builtin_kinds_on_the_default_backend_are_silent(self) -> None:
        assert check_asset_renderers() == []

    def test_an_old_signature_names_the_migration(self) -> None:
        with override_settings(NEXT_FRAMEWORK=_OLD):
            messages = check_asset_renderers()
        assert check_ids(messages) == ["next.E147"]
        assert isinstance(messages[0], Error)
        assert "'css'" in messages[0].msg
        assert "render_link_tag without the request and nonce" in messages[0].msg
        assert "nonce=None" in messages[0].hint

    def test_a_renderer_the_backend_lacks_is_named(self, monkeypatch) -> None:
        monkeypatch.setattr(checks_module, "default_kinds", _babel_kind())
        [message] = check_asset_renderers()
        assert message.id == "next.E147"
        assert "has no render_babel_script_tag method" in message.msg

    def test_a_renderer_taking_every_keyword_is_silent(self, monkeypatch) -> None:
        monkeypatch.setattr(checks_module, "default_kinds", _babel_kind())
        backends = {"STATIC_BACKENDS": [{"BACKEND": f"{_HERE}.OptionsBackend"}]}
        with override_settings(NEXT_FRAMEWORK=backends):
            assert check_asset_renderers() == []

    def test_the_first_backend_that_loads_renders(self) -> None:
        entries = [
            "not a dict",
            {"BACKEND": 3},
            {"BACKEND": "x.Y"},
            {"BACKEND": "django.http.HttpResponse"},
            *_OLD["STATIC_BACKENDS"],
        ]
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": entries}):
            assert check_ids(check_asset_renderers()) == ["next.E147"]
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": entries[:4]}):
            assert check_asset_renderers() == []

    def test_backends_of_the_wrong_shape_fall_back_to_staticfiles(self) -> None:
        with patch.object(checks_module, "next_framework_settings") as framework:
            framework.STATIC_BACKENDS = {}
            assert check_asset_renderers() == []

    def test_a_renderer_without_a_signature_is_given_the_benefit(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK=_OLD),
            patch.object(checks_module.inspect, "signature", side_effect=ValueError),
        ):
            assert check_asset_renderers() == []


class TestInlineAssetBodyLoadableCheck:
    """check_inline_asset_bodies_are_loadable flags lost inline bodies."""

    def test_builtin_kinds_are_silent(self) -> None:
        assert check_inline_asset_bodies_are_loadable() == []

    def test_kind_without_inline_tag_is_silent(self, monkeypatch) -> None:
        verbatim = KindRegistry()
        verbatim.register(
            "mjs", extension=".mjs", slot="scripts", renderer="render_module_tag"
        )
        monkeypatch.setattr(checks_module, "default_kinds", verbatim)
        assert check_inline_asset_bodies_are_loadable() == []

    def test_custom_renderer_kind_is_left_to_w074(self, monkeypatch) -> None:
        custom = KindRegistry()
        custom.register(
            "jsx",
            extension=".jsx",
            slot="scripts",
            renderer="render_babel_script_tag",
            inline_tag="script",
        )
        monkeypatch.setattr(checks_module, "default_kinds", custom)
        assert check_inline_asset_bodies_are_loadable() == []

    def test_mismatched_inline_tag_emits_w076(self, monkeypatch) -> None:
        mismatched = KindRegistry()
        mismatched.register(
            "css",
            extension=".css",
            slot="styles",
            renderer="render_link_tag",
            inline_tag="style",
        )
        mismatched.register(
            "tpl",
            extension=".tpl",
            slot="styles",
            renderer="render_link_tag",
            inline_tag="div",
        )
        monkeypatch.setattr(checks_module, "default_kinds", mismatched)
        messages = check_inline_asset_bodies_are_loadable()
        assert check_ids(messages) == ["next.W076"]
        assert isinstance(messages[0], DjangoWarning)
        assert "'tpl'" in messages[0].msg
        assert "'render_link_tag'" in messages[0].msg
        assert "'div'" in messages[0].msg
        assert "inline bodies carry no client insertion verb" in messages[0].msg

    def test_module_kind_with_an_inline_tag_emits_w076(self, monkeypatch) -> None:
        wrapped_module = KindRegistry()
        wrapped_module.register(
            "vue",
            extension=".vue",
            slot="scripts",
            renderer="render_module_tag",
            inline_tag="script",
        )
        monkeypatch.setattr(checks_module, "default_kinds", wrapped_module)
        assert check_ids(check_inline_asset_bodies_are_loadable()) == ["next.W076"]


class TestReservedJsContextKeyCheck:
    """check_reserved_js_context_keys flags keys the init payload owns."""

    def test_no_keys_is_silent(self, monkeypatch) -> None:
        monkeypatch.setattr(
            checks_module, "iter_serialized_page_context_keys", lambda: iter(())
        )
        monkeypatch.setattr(
            checks_module, "iter_serialized_component_context_keys", lambda: iter(())
        )
        assert check_reserved_js_context_keys() == []

    def test_page_registering_a_reserved_key_emits_w075(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n\n"
            '@page.context("$csrf", serialize=True)\n'
            "def csrf_token():\n"
            '    return {"token": "app"}\n\n\n'
            '@page.context("unread", serialize=True)\n'
            "def unread():\n"
            "    return 3\n"
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_reserved_js_context_keys()
        assert check_ids(messages) == ["next.W075"]
        assert "'$csrf'" in messages[0].msg
        assert "Rename the key." in messages[0].msg

    @pytest.mark.parametrize("key", ["$dev", "$csrf"])
    def test_message_scopes_the_loss_to_the_automatic_payload(
        self, tmp_path, key
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n\n"
            f'@page.context("{key}", serialize=True)\n'
            "def provider():\n"
            "    return False\n"
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_reserved_js_context_keys()
        assert check_ids(messages) == ["next.W075"]
        assert f"'{key}'" in messages[0].msg
        assert f"The framework owns {key} on every render" in messages[0].msg
        assert (
            "the automatically injected payload drops the registered value"
            in messages[0].msg
        )
        assert "reaches window.Next.context" in messages[0].msg
        assert "DEBUG" not in messages[0].msg

    def test_component_registering_a_reserved_key_emits_w075(self, tmp_path) -> None:
        comp_dir = tmp_path / "widget"
        comp_dir.mkdir()
        (comp_dir / "component.djx").write_text("<div/>")
        (comp_dir / "component.py").write_text(
            "from next.components import context\n\n\n"
            '@context("$csrf", serialize=True)\n'
            "def csrf_token():\n"
            '    return {"token": "app"}\n'
        )
        backend = FileComponentsBackend(
            {"DIRS": [str(tmp_path)], "COMPONENTS_DIR": "_components"}
        )
        manager = MagicMock()
        manager.backends = (backend,)
        with patch(
            "next.components.sources.get_components_manager", return_value=manager
        ):
            messages = check_reserved_js_context_keys()
        assert check_ids(messages) == ["next.W075"]
        assert messages[0].msg.startswith("Component context key '$csrf'")
        assert messages[0].obj == str(comp_dir / "component.py")

    def test_unserialized_reserved_key_is_silent(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text(
            "from next.pages import page\n\n\n"
            '@page.context("$csrf")\n'
            "def csrf_token():\n"
            '    return {"token": "app"}\n'
        )
        with patch_checks_router_manager(pages_directory=tmp_path):
            messages = check_reserved_js_context_keys()
        assert messages == []


class TestAppDirectoriesFinderCheck:
    """Which configured finder entry earns ``next.E083`` and which stays silent."""

    @pytest.mark.parametrize("case", APP_FINDER_CASES, ids=lambda case: case.id)
    def test_entry_is_refused_only_when_it_publishes_the_package(
        self, case: AppFinderCase
    ) -> None:
        with override_settings(STATICFILES_FINDERS=[case.path]):
            messages = check_app_directories_finder(app_configs=None)

        assert check_ids(messages) == (["next.E083"] if case.refused else [])

    def test_the_refusal_names_the_entry_and_the_replacement(self) -> None:
        with override_settings(STATICFILES_FINDERS=[PROJECT_APP_DIRECTORIES_FINDER]):
            (message,) = check_app_directories_finder(app_configs=None)

        assert isinstance(message, Error)
        assert PROJECT_APP_DIRECTORIES_FINDER in message.msg
        assert "next.static.NextAppDirectoriesFinder" in message.msg

    def test_the_stock_path_is_rewritten_before_the_check_reads_it(self) -> None:
        with override_settings(STATICFILES_FINDERS=[_APP_DIRECTORIES_PATH]):
            configured = list(settings.STATICFILES_FINDERS)
            messages = check_app_directories_finder(app_configs=None)

        assert _APP_DIRECTORIES_PATH not in configured
        assert messages == []


_CSP_MIDDLEWARE = [
    *settings.MIDDLEWARE,
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
]
_BARE_TEMPLATES = {
    "NEXT_JS_OPTIONS": {
        "script_tag_template": '<script src="{url}"></script>',
        "init_template": "<script{nonce_attr}>Next._init({payload});</script>",
    },
    "STATIC_BACKENDS": [
        {
            "BACKEND": "next.static.StaticFilesBackend",
            "OPTIONS": {"js_tag": '<script src="{url}"></script>', "css_tag": 3},
        },
        "not a dict",
        {"BACKEND": "x.Y", "OPTIONS": "not a dict"},
    ],
}


class TestNonceTemplatesCheck:
    """A custom tag template without `{nonce_attr}` warns while a nonce is active."""

    def test_the_default_resolver_without_csp_middleware_is_silent(self) -> None:
        with override_settings(NEXT_FRAMEWORK=_BARE_TEMPLATES):
            assert check_nonce_templates() == []

    def test_a_switched_off_resolver_is_silent(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={**_BARE_TEMPLATES, "CSP_NONCE": False},
            MIDDLEWARE=_CSP_MIDDLEWARE,
        ):
            assert check_nonce_templates() == []

    def test_csp_middleware_names_every_bare_template(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK=_BARE_TEMPLATES, MIDDLEWARE=_CSP_MIDDLEWARE
        ):
            messages = check_nonce_templates()
        assert check_ids(messages) == ["next.W117", "next.W117"]
        assert "NEXT_JS_OPTIONS['script_tag_template']" in messages[0].msg
        assert "STATIC_BACKENDS[0]['OPTIONS']['js_tag']" in messages[1].msg

    def test_the_default_templates_are_silent(self) -> None:
        with override_settings(MIDDLEWARE=_CSP_MIDDLEWARE):
            assert check_nonce_templates() == []

    def test_options_of_the_wrong_shape_hold_no_template(self) -> None:
        with patch.object(checks_module, "next_framework_settings") as framework:
            framework.NEXT_JS_OPTIONS = []
            framework.STATIC_BACKENDS = {}
            with override_settings(MIDDLEWARE=_CSP_MIDDLEWARE):
                assert check_nonce_templates() == []


class TestNonceOnSharedPagesCheck:
    """`next.W120` names an active nonce taking every shared page private."""

    def _root(self, tmp_path):
        root = tmp_path / "pages"
        root.mkdir(parents=True)
        write_page(root, "shared", "template = 'x'\ncache = 60\n")
        write_page(root, "own", "template = 'x'\n")
        return root

    def test_an_active_nonce_is_w130(self, tmp_path) -> None:
        with (
            routed(self._root(tmp_path)),
            override_settings(MIDDLEWARE=_CSP_MIDDLEWARE),
        ):
            [warning] = check_nonce_on_shared_pages()
        assert warning.id == "next.W120"
        assert "shared" in warning.msg
        assert "CSP_NONCE" in warning.msg

    def test_no_nonce_is_silent(self, tmp_path) -> None:
        with routed(self._root(tmp_path)):
            assert check_nonce_on_shared_pages() == []
        with (
            routed(self._root(tmp_path / "off"), CSP_NONCE=False),
            override_settings(MIDDLEWARE=_CSP_MIDDLEWARE),
        ):
            assert check_nonce_on_shared_pages() == []


class TestScriptPolicyCheck:
    """`next.E130` names a `NEXT_JS_OPTIONS["policy"]` no injection policy matches."""

    @pytest.mark.parametrize(
        "options",
        [{}, {"policy": "manual"}, {"policy": ScriptInjectionPolicy.DISABLED}],
        ids=["unset", "string", "member"],
    )
    def test_a_known_policy_is_silent(self, options) -> None:
        with override_settings(NEXT_FRAMEWORK={"NEXT_JS_OPTIONS": options}):
            assert check_script_policy() == []

    def test_an_unknown_policy_is_e130(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"NEXT_JS_OPTIONS": {"policy": "sometimes"}}
        ):
            [error] = check_script_policy()
        assert error.id == "next.E130"
        assert "'sometimes'" in error.msg
        assert "'auto', 'disabled', 'manual'" in error.msg

    def test_options_of_the_wrong_shape_are_left_to_the_type_check(self) -> None:
        with patch.object(checks_module, "next_framework_settings") as framework:
            framework.NEXT_JS_OPTIONS = []
            assert check_script_policy() == []


class TestTagTemplatesFormatCheck:
    """`next.E139` names a custom tag template `.format` cannot fill."""

    def test_formattable_templates_are_silent(self) -> None:
        with override_settings(NEXT_FRAMEWORK=_BARE_TEMPLATES):
            assert check_tag_templates_format() == []

    def test_every_broken_template_is_named(self) -> None:
        framework = {
            "NEXT_JS_OPTIONS": {
                "init_template": "<script>{payload}{</script>",
                "preload_template": "<link {rel} href='{url}'>",
                "script_tag_template": "",
            },
            "STATIC_BACKENDS": [
                {"OPTIONS": {"css_tag": "<link href='{0}'>", "js_tag": None}}
            ],
        }
        with override_settings(NEXT_FRAMEWORK=framework):
            errors = check_tag_templates_format()
        assert check_ids(errors) == ["next.E139"] * 3
        assert "NEXT_JS_OPTIONS['preload_template']" in errors[0].msg
        assert "{url}, {nonce_attr}" in errors[0].msg
        assert "KeyError: 'rel'" in errors[0].msg
        assert "NEXT_JS_OPTIONS['init_template']" in errors[1].msg
        assert "{payload}, {nonce_attr}" in errors[1].msg
        assert "STATIC_BACKENDS[0]['OPTIONS']['css_tag']" in errors[2].msg
        assert "Double every literal brace" in errors[0].hint


class TestRuntimeBundlesDeployedCheck:
    """`next.W090` names a runtime bundle the deployed storage cannot serve."""

    def test_it_is_a_deployment_check(self) -> None:
        assert check_runtime_bundles_deployed in registry.get_checks(
            include_deployment_checks=True
        )
        assert check_runtime_bundles_deployed not in registry.get_checks()

    def test_built_and_collected_bundles_are_silent(self) -> None:
        with (
            patch.object(checks_module.finders, "find", return_value="/src/x.js"),
            patch.object(
                checks_module.staticfiles_storage, "url", return_value="/s/x.js"
            ),
        ):
            assert check_runtime_bundles_deployed() == []

    def test_an_unbuilt_runtime_is_w090_and_the_dev_chunk_is_not_asked(self) -> None:
        with patch.object(checks_module.finders, "find", return_value=[]) as find:
            warnings = check_runtime_bundles_deployed()
        assert check_ids(warnings) == ["next.W090"] * 5
        assert warnings[0].msg.startswith("next/next.min.js: no static files finder")
        assert "collectstatic" in warnings[0].hint
        asked = [call.args[0] for call in find.call_args_list]
        assert "next/next.dev.min.js" not in asked

    def test_an_uncollected_chunk_is_w090(self) -> None:
        def url(name: str) -> str:
            if name == "next/next.sse.min.js":
                msg = "Missing staticfiles manifest entry"
                raise ValueError(msg)
            return f"/static/{name}"

        with (
            patch.object(checks_module.finders, "find", return_value="/src/x.js"),
            patch.object(checks_module.staticfiles_storage, "url", side_effect=url),
        ):
            [warning] = check_runtime_bundles_deployed()
        assert warning.msg.startswith("next/next.sse.min.js: the static files storage")
