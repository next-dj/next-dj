import json
import threading
from unittest import mock

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.test.utils import override_script_prefix
from django.urls import NoReverseMatch

from next.pages import page
from next.partial.headers import CONTENT_TYPE
from next.partial.manager import partial_backend_manager
from next.router import (
    ChromeDigest,
    ClientLayouts,
    Cut,
    LoadShed,
    NavigationCut,
    PrefetchShed,
    ScannedPage,
    VerifiedCut,
    manager as router_module,
    router_backend_loaded,
)
from next.router.manager import (
    ClientRouterManager,
    client_router,
    exclude_prefixes,
    forget_router,
    router_entry,
    speculation_rules,
)
from next.testing import capture_signals, override_next_settings
from next.urls import route_manager
from next.urls.scope import scope_url
from tests.support import I18N_ROUTED
from tests.support.races import LockWonByAnotherThread


class _Cut(NavigationCut):
    def cut(self, declared: ClientLayouts | None, shell: ScannedPage) -> Cut:
        return VerifiedCut().cut(declared, shell)


class _Shed(PrefetchShed):
    def waited(self, request: object, page_path: object) -> float | None:
        return None


class TestBackends:
    """The cut, the digest and the shed are built once from their settings paths."""

    def test_the_defaults(self) -> None:
        manager = ClientRouterManager()
        assert type(manager.cut()) is VerifiedCut
        assert manager.cut() is manager.cut()
        assert type(manager.digest()) is ChromeDigest
        assert manager.digest() is manager.digest()
        assert type(manager.shed()) is LoadShed
        assert manager.shed() is manager.shed()

    def test_a_named_cut(self) -> None:
        with override_next_settings(ROUTER={"CUT": f"{__name__}._Cut"}):
            assert type(ClientRouterManager().cut()) is _Cut

    def test_a_named_shed(self) -> None:
        with override_next_settings(ROUTER={"SHED": f"{__name__}._Shed"}):
            assert type(ClientRouterManager().shed()) is _Shed

    def test_an_unusable_shed_raises(self) -> None:
        with (
            override_next_settings(ROUTER={"SHED": "next.router.VerifiedCut"}),
            pytest.raises(ImproperlyConfigured, match="SHED"),
        ):
            ClientRouterManager().shed()

    @pytest.mark.parametrize("value", [123, "", None, ["next.router.VerifiedCut"]])
    def test_a_misfit_seam_raises(self, value: object) -> None:
        with (
            pytest.raises(ImproperlyConfigured, match=r"\['CUT'\] must be"),
            override_next_settings(ROUTER={"CUT": value, "DIGEST": value}),
        ):
            ClientRouterManager().cut()

    def test_an_unusable_digest_raises(self) -> None:
        with (
            override_next_settings(ROUTER={"DIGEST": "next.router.VerifiedCut"}),
            pytest.raises(ImproperlyConfigured, match="DIGEST"),
        ):
            ClientRouterManager().digest()

    def test_each_backend_is_announced_once_with_its_entry(self) -> None:
        manager = ClientRouterManager()
        with capture_signals(router_backend_loaded) as recorder:
            backends = manager.cut(), manager.digest(), manager.shed()
            manager.cut()
        events = recorder.events
        assert [(event.sender, event.kwargs["config"]) for event in events] == [
            (VerifiedCut, {"CUT": "next.router.VerifiedCut"}),
            (ChromeDigest, {"DIGEST": "next.router.ChromeDigest"}),
            (LoadShed, {"SHED": "next.router.LoadShed"}),
        ]
        assert [event.kwargs["instance"] for event in events] == list(backends)

    def test_a_racing_call_keeps_the_backends_the_winner_built(self) -> None:
        manager = ClientRouterManager()
        fresh = ClientRouterManager()
        built = fresh.cut(), fresh.digest(), fresh.shed()
        winner = router_module._Seams(*built, paths=("", "", ""))
        manager._lock = LockWonByAnotherThread(manager, "_seams", value=winner)
        assert (manager.cut(), manager.digest(), manager.shed()) == built

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
        assert entry["routes"] == scope_url()
        assert "scope" not in entry
        assert entry["links"] == ["nav", "[role=navigation]", "[data-next-nav]"]
        assert entry["homes"] == ["/"]
        assert (entry["prefetch_ttl"], entry["prefetch_shell_ttl"]) == (30, 300)
        assert entry["progress"] is True

    def test_whole_seconds_are_sent_as_integers(self) -> None:
        encoded = router_entry()
        assert '"prefetch_ttl":30,' in encoded
        assert '"prefetch_delay":0.065,' in encoded

    @pytest.mark.parametrize("scope", ["all", "opt_in"])
    def test_another_scope_is_sent_in_place_of_the_routes(self, scope: str) -> None:
        with override_next_settings(ROUTER={"SCOPE": scope}):
            entry = json.loads(router_entry())
        assert entry["scope"] == scope
        assert "routes" not in entry

    def test_the_routes_are_none_without_the_framework_urls(self) -> None:
        with override_settings(ROOT_URLCONF="tests.router.urls_exclude"):
            assert json.loads(router_entry())["routes"] is None

    def test_a_routes_reload_moves_the_routes_url(self) -> None:
        held = client_router.entry()
        with mock.patch("next.router.manager.scope_url", return_value="/moved/"):
            assert client_router.entry() == held
            route_manager.reload()
            assert json.loads(client_router.entry())["routes"] == "/moved/"

    def test_the_progress_bar_switch_is_sent(self) -> None:
        with override_next_settings(ROUTER={"PROGRESS": False}):
            assert json.loads(router_entry())["progress"] is False

    @pytest.mark.parametrize(
        ("written", "sent"), [(None, "mouse"), ("all", "all"), ("off", "off")]
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
    def test_containers_outside_the_grammar_raise(self, containers: list[str]) -> None:
        with (
            pytest.raises(ImproperlyConfigured, match=r"\['CONTAINERS'\] must be"),
            override_next_settings(ROUTER={"ACTIVE_LINKS": {"CONTAINERS": containers}}),
        ):
            router_entry()

    def test_no_containers_while_the_pass_is_off(self) -> None:
        with override_next_settings(ROUTER={"ACTIVE_LINKS": {"ENABLED": False}}):
            entry = json.loads(router_entry())
        assert entry["links"] is None
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

    def test_an_unset_media_url_under_a_script_prefix_adds_nothing(self) -> None:
        with override_settings(MEDIA_URL=""), override_script_prefix("/app/"):
            assert "/app/" not in exclude_prefixes()

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
                    "SCOPE": "opt_in",
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

    def test_the_pages_scope_names_the_page_routes(self) -> None:
        table = mock.Mock()
        table.patterns.side_effect = lambda *, page: (
            ["/blog/{:p0}/"] if page else ["/blog/feed/"]
        )
        with (
            override_next_settings(ROUTER={"SPECULATION": "moderate"}),
            mock.patch.object(router_module, "route_scope", return_value=table),
        ):
            found = json.loads(speculation_rules())
        (entry,) = found["prefetch"]
        pages, others, _, _ = entry["where"]["and"]
        assert pages == {"href_matches": ["/blog/{:p0}/"]}
        assert others == {"not": {"href_matches": ["/blog/feed/"]}}

    def test_the_pages_scope_without_a_page_emits_nothing(self) -> None:
        table = mock.Mock()
        table.patterns.return_value = []
        with (
            override_next_settings(ROUTER={"SPECULATION": "moderate"}),
            mock.patch.object(router_module, "route_scope", return_value=table),
        ):
            assert speculation_rules() == ""

    def test_the_rules_are_held(self) -> None:
        manager = ClientRouterManager()
        with mock.patch.object(
            router_module, "speculation_rules", return_value="{}"
        ) as built:
            assert manager.speculation() == manager.speculation() == "{}"
        assert built.call_count == 1

    def test_a_document_reads_the_entry_and_the_rules_together(self) -> None:
        manager = ClientRouterManager()
        with mock.patch.object(
            router_module, "speculation_rules", return_value="{}"
        ) as built:
            entry, rules = manager.document()
            assert manager.entry() is entry
            assert manager.speculation() is rules
        assert rules == "{}"
        assert built.call_count == 1


class TestForget:
    """A reload drops the backends and recomposes pages once the router switches."""

    def test_a_switch_clears_the_templates_once(self) -> None:
        with mock.patch.object(page, "clear_template_caches") as cleared:
            client_router.composed = None
            forget_router()
            forget_router()
        assert cleared.call_count == 1

    @pytest.mark.parametrize(
        "setting", ["STATIC_URL", "LANGUAGES", "FORCE_SCRIPT_NAME"]
    )
    def test_a_setting_change_drops_the_entry(self, setting) -> None:
        first = client_router.entry()
        assert client_router.entry() is first
        with override_settings(**{setting: getattr(settings, setting)}):
            assert client_router.entry() is not first
