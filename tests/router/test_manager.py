import json
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.urls import NoReverseMatch, clear_script_prefix, set_script_prefix

from next.pages import page
from next.partial.headers import CONTENT_TYPE
from next.partial.manager import partial_backend_manager
from next.router import (
    ChromeDigest,
    Cut,
    Declaration,
    NavigationCut,
    Shell,
    VerifiedCut,
    manager as router_module,
)
from next.router.manager import (
    RouterManager,
    exclude_prefixes,
    forget_router,
    forget_router_entry,
    router_entry,
    router_manager,
    speculation_rules,
)
from next.testing import override_next_settings
from tests.support import I18N_ROUTED


class _Cut(NavigationCut):
    def cut(self, declared: Declaration | None, shell: Shell) -> Cut:
        return VerifiedCut().cut(declared, shell)


class TestBackends:
    """The cut and the digest are built once from their settings paths."""

    def test_the_defaults(self) -> None:
        manager = RouterManager()
        assert type(manager.cut()) is VerifiedCut
        assert manager.cut() is manager.cut()
        assert type(manager.digest()) is ChromeDigest
        assert manager.digest() is manager.digest()

    def test_a_named_cut(self) -> None:
        with override_next_settings(ROUTER={"CUT": f"{__name__}._Cut"}):
            assert type(RouterManager().cut()) is _Cut

    def test_an_unusable_digest_raises(self) -> None:
        with (
            override_next_settings(ROUTER={"DIGEST": "next.router.VerifiedCut"}),
            pytest.raises(ImproperlyConfigured),
        ):
            RouterManager().digest()


class TestEntry:
    """`$router` carries the settings the client reads and the excluded prefixes."""

    def test_the_entry(self) -> None:
        entry = json.loads(router_entry())
        assert entry["scope"] == "all"
        assert entry["current"] == ["nav", "[role=navigation]", "[data-next-nav]"]
        assert entry["homes"] == ["/"]
        assert entry["max_layouts"] == 32
        assert (entry["prefetch_ttl"], entry["prefetch_shell_ttl"]) == (30.0, 300.0)
        assert "progress" not in entry

    @pytest.mark.parametrize(
        ("written", "sent"),
        [(None, "mouse"), ("all", "all"), ("off", "off"), ("touch", "mouse")],
    )
    def test_the_press_mode_is_sent(self, written, sent) -> None:
        router = {} if written is None else {"PRESS": written}
        with override_next_settings(ROUTER=router):
            entry = json.loads(router_entry())
        assert entry["press"] == sent

    def test_the_stock_content_type_stays_out(self) -> None:
        assert partial_backend_manager.get().content_type == CONTENT_TYPE
        assert "type" not in json.loads(router_entry())

    def test_another_partial_backend_names_its_content_type(self) -> None:
        with mock.patch.object(partial_backend_manager, "get") as get_backend:
            get_backend.return_value.content_type = "text/vnd.turbo-stream.html"
            entry = json.loads(router_entry())
        assert entry["type"] == "text/vnd.turbo-stream.html"

    @pytest.mark.parametrize("containers", [[".menu"], ["nav", "#top"], ["nav a"]])
    def test_containers_outside_the_grammar_send_the_defaults(
        self, containers: list[str]
    ) -> None:
        with override_next_settings(ROUTER={"CURRENT": {"CONTAINERS": containers}}):
            entry = json.loads(router_entry())
        assert entry["current"] == ["nav", "[role=navigation]", "[data-next-nav]"]

    def test_no_containers_while_the_pass_is_off(self) -> None:
        with override_next_settings(ROUTER={"CURRENT": {"ENABLED": False}}):
            entry = json.loads(router_entry())
        assert entry["current"] is None
        assert entry["homes"] is None

    @override_settings(**I18N_ROUTED)
    def test_the_homes_name_each_language_root(self) -> None:
        assert json.loads(router_entry())["homes"] == ["/", "/de"]

    def test_exclusions(self) -> None:
        with (
            override_settings(
                STATIC_URL="https://cdn.example.com/assets/", MEDIA_URL="media/"
            ),
            override_next_settings(ROUTER={"EXCLUDE": ["/files/", "/"]}),
            mock.patch.object(router_module, "reverse", return_value="/admin/"),
            mock.patch.object(router_module.apps, "is_installed", return_value=True),
        ):
            assert exclude_prefixes() == ("/assets/", "/media/", "/admin/", "/files/")

    def test_an_unmounted_admin_adds_nothing(self) -> None:
        with (
            override_settings(MEDIA_URL=""),
            mock.patch.object(router_module.apps, "is_installed", return_value=True),
            mock.patch.object(router_module, "reverse", side_effect=NoReverseMatch),
        ):
            assert "/admin/" not in exclude_prefixes()

    def test_an_unset_static_url_adds_nothing(self) -> None:
        with override_settings(STATIC_URL=None, MEDIA_URL="/media/"):
            assert router_module.asset_prefixes() == ("/media/",)

    def test_no_admin_installed(self) -> None:
        with mock.patch.object(router_module.apps, "is_installed", return_value=False):
            assert router_module._admin_prefix() is None

    def test_the_entry_is_held(self) -> None:
        manager = RouterManager()
        assert manager.entry() is manager.entry()

    @override_settings(**I18N_ROUTED)
    def test_each_script_prefix_holds_its_own_entry(self) -> None:
        manager = RouterManager()
        root = manager.entry()
        set_script_prefix("/app/")
        try:
            mounted = manager.entry()
            assert manager.entry() is mounted
        finally:
            clear_script_prefix()
        assert json.loads(mounted)["homes"] == ["/", "/app", "/app/de"]
        assert manager.entry() is root

    def test_a_crowd_of_places_starts_over(self) -> None:
        manager = RouterManager()
        first = manager.entry()
        try:
            for index in range(64):
                set_script_prefix(f"/p{index}/")
                manager.entry()
        finally:
            clear_script_prefix()
        assert manager.entry() is not first


class TestSpeculation:
    """The rules follow `SPECULATION` and the exclusions, and are built once."""

    def test_unset_is_empty(self) -> None:
        assert speculation_rules() == ""

    def test_the_rules(self) -> None:
        with (
            override_next_settings(
                ROUTER={
                    "SPECULATION": {"eagerness": "conservative"},
                    "SCOPE": "opt-in",
                    "EXCLUDE": ["/files/"],
                }
            ),
            mock.patch.object(router_module.apps, "is_installed", return_value=False),
        ):
            found = json.loads(speculation_rules())
        (entry,) = found["prefetch"]
        assert entry["eagerness"] == "conservative"
        _, unowned, _ = entry["where"]["and"]
        assert {"href_matches": "/files/*"} in unowned["or"]
        assert "not" in unowned["or"][-1]

    def test_the_rules_are_held(self) -> None:
        manager = RouterManager()
        with mock.patch.object(
            router_module, "speculation_rules", return_value="{}"
        ) as built:
            assert manager.speculation() == manager.speculation() == "{}"
        assert built.call_count == 1


class TestForget:
    """A reload drops the backends and recomposes pages once the router switches."""

    def test_a_switch_clears_the_templates_once(self) -> None:
        with mock.patch.object(page, "clear_template_caches") as cleared:
            router_manager.composed = None
            forget_router()
            forget_router()
        assert cleared.call_count == 1

    def test_an_entry_setting_drops_the_entry(self) -> None:
        first = router_manager.entry()
        forget_router_entry(setting="DEBUG")
        assert router_manager.entry() is first
        forget_router_entry(setting="STATIC_URL")
        assert router_manager.entry() is not first

    @pytest.mark.parametrize("setting", ["LANGUAGES", "FORCE_SCRIPT_NAME"])
    def test_a_home_setting_drops_the_entry(self, setting) -> None:
        first = router_manager.entry()
        forget_router_entry(setting=setting)
        assert router_manager.entry() is not first
