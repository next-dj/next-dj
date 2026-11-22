import json
import threading
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.test.utils import override_script_prefix
from django.urls import NoReverseMatch

from next.pages import page
from next.partial.headers import CONTENT_TYPE
from next.partial.manager import partial_backend_manager
from next.router import (
    ChromeDigest,
    Cut,
    Declaration,
    NavigationCut,
    ScannedShell,
    VerifiedCut,
    manager as router_module,
)
from next.router.manager import (
    ClientRouterManager,
    client_router,
    exclude_prefixes,
    forget_router,
    forget_router_entry,
    router_entry,
    speculation_rules,
)
from next.testing import override_next_settings
from tests.support import I18N_ROUTED
from tests.support.races import LockWonByAnotherThread


class _Cut(NavigationCut):
    def cut(self, declared: Declaration | None, shell: ScannedShell) -> Cut:
        return VerifiedCut().cut(declared, shell)


class TestBackends:
    """The cut and the digest are built once from their settings paths."""

    def test_the_defaults(self) -> None:
        manager = ClientRouterManager()
        assert type(manager.cut()) is VerifiedCut
        assert manager.cut() is manager.cut()
        assert type(manager.digest()) is ChromeDigest
        assert manager.digest() is manager.digest()

    def test_a_named_cut(self) -> None:
        with override_next_settings(ROUTER={"CUT": f"{__name__}._Cut"}):
            assert type(ClientRouterManager().cut()) is _Cut

    @pytest.mark.parametrize("value", [123, "", None, ["next.router.VerifiedCut"]])
    def test_a_misfit_seam_keeps_the_shipped_backend(self, value: object) -> None:
        with override_next_settings(ROUTER={"CUT": value, "DIGEST": value}):
            manager = ClientRouterManager()
            assert type(manager.cut()) is VerifiedCut
            assert type(manager.digest()) is ChromeDigest

    def test_an_unusable_digest_raises(self) -> None:
        with (
            override_next_settings(ROUTER={"DIGEST": "next.router.VerifiedCut"}),
            pytest.raises(ImproperlyConfigured, match="DIGEST"),
        ):
            ClientRouterManager().digest()

    def test_a_racing_call_keeps_the_backends_the_winner_built(self) -> None:
        manager = ClientRouterManager()
        built = ClientRouterManager().cut(), ClientRouterManager().digest()
        winner = router_module._Seams(*built)
        manager._lock = LockWonByAnotherThread(manager, "_seams", value=winner)
        assert (manager.cut(), manager.digest()) == built

    def test_a_forget_waits_out_a_build_under_way(self) -> None:
        manager = ClientRouterManager()
        building, release = threading.Event(), threading.Event()
        build = router_module._seams

        def slow() -> router_module._Seams:
            building.set()
            release.wait(5)
            return build()

        with mock.patch.object(router_module, "_seams", side_effect=slow):
            builder = threading.Thread(target=manager.cut)
            builder.start()
            building.wait(5)
            forgetter = threading.Thread(target=manager.forget)
            forgetter.start()
            forgetter.join(0.05)
            release.set()
            builder.join(5)
            forgetter.join(5)
        assert manager._seams is None

    def test_a_forked_child_starts_with_a_fresh_lock(self) -> None:
        manager = ClientRouterManager()
        manager.cut()
        held = manager._lock
        manager.forked()
        assert manager._lock is not held
        assert manager._seams is None


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
        manager = ClientRouterManager()
        assert manager.entry() is manager.entry()

    @override_settings(**I18N_ROUTED)
    def test_each_script_prefix_holds_its_own_entry(self) -> None:
        manager = ClientRouterManager()
        root = manager.entry()
        with override_script_prefix("/app/"):
            mounted = manager.entry()
            assert manager.entry() is mounted
        assert json.loads(mounted)["homes"] == ["/", "/app", "/app/de"]
        assert manager.entry() is root

    def test_a_crowd_of_places_starts_over(self) -> None:
        manager = ClientRouterManager()
        first = manager.entry()
        for index in range(64):
            with override_script_prefix(f"/p{index}/"):
                manager.entry()
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
        manager = ClientRouterManager()
        with mock.patch.object(
            router_module, "speculation_rules", return_value="{}"
        ) as built:
            assert manager.speculation() == manager.speculation() == "{}"
        assert built.call_count == 1


class TestForget:
    """A reload drops the backends and recomposes pages once the router switches."""

    def test_a_switch_clears_the_templates_once(self) -> None:
        with mock.patch.object(page, "clear_template_caches") as cleared:
            client_router.composed = None
            forget_router()
            forget_router()
        assert cleared.call_count == 1

    def test_an_entry_setting_drops_the_entry(self) -> None:
        first = client_router.entry()
        forget_router_entry(setting="DEBUG")
        assert client_router.entry() is first
        forget_router_entry(setting="STATIC_URL")
        assert client_router.entry() is not first

    @pytest.mark.parametrize("setting", ["LANGUAGES", "FORCE_SCRIPT_NAME"])
    def test_a_home_setting_drops_the_entry(self, setting) -> None:
        first = client_router.entry()
        forget_router_entry(setting=setting)
        assert client_router.entry() is not first
