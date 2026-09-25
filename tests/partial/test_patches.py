from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import override_settings
from django.utils.functional import lazy

import next.pages
import next.partial
import next.partial.errors
import next.partial.patches
from next.partial import Asset, FormMeta, Patches, PatchResponse
from next.partial.errors import (
    BuiltinPatchOpError,
    ForeignPageNotAuthorizedError,
    ReservedPatchKeyError,
)
from next.partial.headers import CONTENT_TYPE
from next.partial.render import ZoneRenderResult
from next.static import KindRegistry, StaticAsset
from next.static.manager import default_manager
from next.testing import override_next_settings
from tests.support import (
    BUILD_MANIFEST,
    MANIFEST_BACKENDS,
    BuildManifestBackend,
    file_router_config_entry,
    partial_request,
    write_page_chain,
)


INHERITING_ROOT = """
from next.pages import page


@page.context("board", inherit_context=True)
def board():
    return "Kanban"


@page.metadata(inherit=True)
def root_meta(board):
    return {"title": {"template": "{title} | " + board}}
"""
DENYING_LEAF = """
from django.http import HttpResponseForbidden

metadata = {"title": "Leaf"}


def render():
    return HttpResponseForbidden()
"""
LEAF_TITLE = 'template = "<p>Leaf</p>"\nmetadata = {"title": "Leaf"}\n'
STATIC_ROOT = 'metadata = {"title": {"template": "{title} | Static"}}\n'
ZONED_LEAF = """
from next.pages import page


@page.context("user")
def user():
    return "Ann"


@page.metadata
def leaf_meta(user):
    return {"title": user}
"""


def _routed(root: Path) -> override_settings:
    """Route the page tree under `root` as the only page backend."""
    entry = file_router_config_entry(pages_dir=root)
    return override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]})


def _builder_for(root: Path, page_path: Path) -> Patches:
    """Return a builder whose posted origin is the URL routing `page_path`."""
    return Patches(
        partial_request(f"/{page_path.parent.relative_to(root).as_posix()}/")
    )


class TestAddAssetResolvesLoad:
    """`add_asset` stamps the insertion verb the kind registry knows."""

    @pytest.mark.parametrize(
        ("kind", "expected"), [("css", "link"), ("js", "script"), ("module", "module")]
    )
    def test_builtin_kind_carries_its_verb(self, kind: str, expected: str) -> None:
        envelope = Patches.versioned("v1").add_asset(kind, f"/a.{kind}").envelope()
        assert envelope.assets[0].as_dict()["load"] == expected

    def test_unregistered_kind_travels_without_a_verb(self) -> None:
        envelope = Patches.versioned("v1").add_asset("whatever", "/a.bin").envelope()
        assert envelope.assets[0].load is None
        assert envelope.assets[0].as_dict() == {"kind": "whatever", "url": "/a.bin"}

    def test_custom_renderer_kind_travels_without_a_verb(self, monkeypatch) -> None:
        registry = KindRegistry()
        registry.register(
            "jsx", extension=".jsx", slot="scripts", renderer="render_babel_tag"
        )
        monkeypatch.setattr(next.partial.patches, "default_kinds", registry)
        envelope = Patches.versioned("v1").add_asset("jsx", "/a.jsx").envelope()
        assert "load" not in envelope.assets[0].as_dict()

    def test_inline_asset_carries_its_verb(self) -> None:
        envelope = (
            Patches.versioned("v1").add_asset("css", "", inline=".x {}").envelope()
        )
        assert envelope.assets[0].as_dict()["load"] == "link"

    def test_inline_module_body_travels_without_a_verb(self) -> None:
        envelope = (
            Patches.versioned("v1")
            .add_asset("module", "", inline="export const a = 1;")
            .envelope()
        )
        assert envelope.assets[0].load is None
        assert "load" not in envelope.assets[0].as_dict()

    def test_inline_body_of_a_kind_without_a_wrapper_has_no_verb(
        self, monkeypatch
    ) -> None:
        registry = KindRegistry()
        registry.register(
            "snippet", extension=".snip", slot="scripts", renderer="render_script_tag"
        )
        monkeypatch.setattr(next.partial.patches, "default_kinds", registry)
        envelope = (
            Patches.versioned("v1").add_asset("snippet", "", inline="x()").envelope()
        )
        assert envelope.assets[0].load is None
        assert envelope.assets[0].as_dict()["inline"] == "x()"


class TestAddAssetFollowsTheBackendRewrite:
    """A hand-written asset reaches the client through the same hook a page does.

    The envelope ships bare URLs the client inserts itself, so a manifest built
    around the hook would hand a rewriting deployment an unresolvable URL.
    """

    def test_a_url_passes_through_the_backend_hook(self, monkeypatch) -> None:
        def prefix(url: str, *, request=None) -> str:
            assert request is not None
            return f"/pfx{url}"

        monkeypatch.setattr(default_manager, "asset_url", prefix)
        envelope = (
            Patches(partial_request("/"))
            .add_asset("css", "/static/app/x.css")
            .envelope()
        )
        assert envelope.assets[0].url == "/pfx/static/app/x.css"

    def test_an_inline_body_never_reaches_the_hook(self, monkeypatch) -> None:
        asked = []

        def record(url: str, *, request=None) -> str:
            asked.append(url)
            return url

        monkeypatch.setattr(default_manager, "asset_url", record)
        envelope = (
            Patches(partial_request("/"))
            .add_asset("css", "", inline=".x {}")
            .envelope()
        )
        assert envelope.assets[0].url == ""
        assert asked == []


class TestAddAssetResolvesTheReference:
    """A patch envelope and a full render put a value through the same resolution."""

    def test_a_name_reaches_the_envelope_as_a_public_url(self) -> None:
        envelope = (
            Patches(partial_request("/")).add_asset("css", "css/app.css").envelope()
        )
        assert envelope.assets[0].url == "/static/css/app.css"

    def test_a_ready_url_reaches_the_envelope_unchanged(self) -> None:
        envelope = (
            Patches(partial_request("/"))
            .add_asset("css", "https://cdn/app.css")
            .envelope()
        )
        assert envelope.assets[0].url == "https://cdn/app.css"

    def test_an_inline_body_never_reaches_the_resolver(self, monkeypatch) -> None:
        asked = []

        def record(reference: str) -> str:
            asked.append(reference)
            return reference

        monkeypatch.setattr(default_manager, "resolve_url", record)
        envelope = (
            Patches(partial_request("/"))
            .add_asset("css", "", inline=".x {}")
            .envelope()
        )
        assert envelope.assets[0].url == ""
        assert asked == []


class TestManifestBackendAnswersTheSameOnBothPaths:
    """A backend mapping names to build outputs agrees across page and patch.

    The build URL is no staticfiles name, so a second pass through the manifest
    would quietly fall back to storage and hand the client a different URL.
    """

    def test_a_patch_asset_carries_the_build_url_the_page_carries(self) -> None:
        with override_next_settings(**MANIFEST_BACKENDS):
            page_url = default_manager.resolve_url("css/app.css")
            envelope = (
                Patches(partial_request("/")).add_asset("css", "css/app.css").envelope()
            )

        assert page_url == BUILD_MANIFEST["css/app.css"]
        assert envelope.assets[0].url == page_url

    def test_a_zone_asset_is_recorded_rather_than_resolved_again(self) -> None:
        with override_next_settings(**MANIFEST_BACKENDS):
            collector = default_manager.create_collector()
            page_url = default_manager.resolve_url("css/app.css")
            collector.add(StaticAsset(url=page_url, kind="css"))
            backend = default_manager.default_backend
            asked = list(backend.resolved)
            envelope = (
                Patches.versioned("v1")
                .absorb_zone_result(
                    ZoneRenderResult(html={}, bodies={}, collector=collector)
                )
                .envelope()
            )

        assert isinstance(backend, BuildManifestBackend)
        assert envelope.assets[0].url == page_url
        assert backend.resolved == asked


class TestPatchesBuilder:
    """The minimal builder emits HTML and HTML-less verbs in order."""

    def test_morph_is_the_default_verb(self) -> None:
        envelope = (
            Patches.versioned("v1").morph({"zone": "list"}, "<div></div>").envelope()
        )
        assert envelope.ops[0].as_dict() == {
            "op": "morph",
            "target": {"zone": "list"},
            "html": "<div></div>",
        }

    def test_morph_form_extract_marks_payload(self) -> None:
        envelope = (
            Patches.versioned("v1").morph_form("ab12", "<html></html>").envelope()
        )
        assert envelope.ops[0].as_dict()["extract"] is True

    def test_morph_form_extract_morphs_by_uid(self) -> None:
        envelope = (
            Patches.versioned("v1").morph_form("ab12", "<form></form>").envelope()
        )
        assert envelope.ops[0].as_dict() == {
            "op": "morph",
            "target": {"form": "ab12"},
            "html": "<form></form>",
            "extract": True,
        }

    def test_morph_facade_form_delegates_to_morph_form(self) -> None:
        facade = (
            Patches.versioned("v1").morph(form="ab12", html="<form></form>").envelope()
        )
        direct = Patches.versioned("v1").morph_form("ab12", "<form></form>").envelope()
        assert facade.ops[0].as_dict() == direct.ops[0].as_dict()

    def test_morph_rejects_an_unknown_selector(self) -> None:
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            Patches.versioned("v1").morph(widget="ab12")

    def test_morph_rejects_conflicting_selectors(self) -> None:
        with pytest.raises(TypeError, match=r"\['form'\]"):
            Patches.versioned("v1").morph(zone="list", form="ab12")

    def test_morph_rejects_a_none_valued_selector(self) -> None:
        with pytest.raises(TypeError, match="needs a target mapping"):
            Patches.versioned("v1").morph(zone=None)

    def test_replace(self) -> None:
        envelope = (
            Patches.versioned("v1").replace({"zone": "list"}, "<div></div>").envelope()
        )
        assert envelope.ops[0].as_dict() == {
            "op": "replace",
            "target": {"zone": "list"},
            "html": "<div></div>",
        }

    def test_inner(self) -> None:
        envelope = Patches.versioned("v1").inner({"zone": "list"}, "<p></p>").envelope()
        assert envelope.ops[0].op == "inner"
        assert envelope.ops[0].html == "<p></p>"

    def test_remove(self) -> None:
        envelope = Patches.versioned("v1").remove({"css": "#row-1"}).envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "remove",
            "target": {"css": "#row-1"},
        }

    def test_event_default_detail(self) -> None:
        envelope = Patches.versioned("v1").event("saved").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "event",
            "name": "saved",
            "detail": {},
        }

    def test_event_with_detail(self) -> None:
        envelope = Patches.versioned("v1").event("saved", {"id": 7}).envelope()
        assert envelope.ops[0].as_dict()["detail"] == {"id": 7}

    def test_chaining_preserves_order(self) -> None:
        envelope = (
            Patches.versioned("v1")
            .replace({"zone": "a"}, "<a></a>")
            .remove({"zone": "b"})
            .event("done")
            .envelope()
        )
        assert [op.op for op in envelope.ops] == ["replace", "remove", "event"]

    def test_assets_and_form(self) -> None:
        form = FormMeta(uid="ab12", valid=True)
        envelope = (
            Patches.versioned("v1").add_asset("css", "/x.css").set_form(form).envelope()
        )
        assert envelope.assets[0] == Asset(kind="css", url="/x.css", load="link")
        assert envelope.form is form

    def test_add_asset_records_an_inline_body(self) -> None:
        envelope = (
            Patches.versioned("v1").add_asset("css", "", inline=".x {}").envelope()
        )
        assert envelope.assets[0] == Asset(
            kind="css", url="", inline=".x {}", load="link"
        )

    def test_add_context_records_a_context_op(self) -> None:
        envelope = Patches.versioned("v1")._add_context({"unread": 3}).envelope()
        assert envelope.ops[0].as_dict() == {"op": "context", "data": {"unread": 3}}

    def test_version_carried(self) -> None:
        assert Patches.versioned("9f3c").envelope().version == "9f3c"

    def test_versioned_echo_of_rides_as_request_id(self) -> None:
        envelope = Patches.versioned("v1", echo_of="r1").envelope()
        assert envelope.request_id == "r1"

    def test_versioned_carries_no_request_id_by_default(self) -> None:
        assert Patches.versioned("v1").envelope().request_id is None

    def test_the_constructor_pins_a_version_like_versioned(self) -> None:
        request = partial_request()
        pinned = Patches(request, version="9f3c", echo_of="r1").envelope()
        assert pinned.version == "9f3c"
        assert pinned.request_id == "r1"

    def test_a_request_free_builder_needs_no_versioned_sugar(self) -> None:
        assert Patches(None, version="9f3c").envelope().version == "9f3c"


class TestMeta:
    """`meta` ships the title the origin page would render, and nothing else."""

    def test_the_ancestor_template_wraps_the_title_of_the_verb(self) -> None:
        envelope = Patches(partial_request("/titled/leaf/")).meta("Wallets").envelope()
        assert envelope.ops[0].as_dict() == {"op": "meta", "title": "Wallets · Site"}
        assert envelope.ops[0].extras == {"title": "Wallets · Site"}

    def test_the_template_applies_only_to_descendants(self) -> None:
        envelope = Patches(partial_request("/titled/")).meta("Wallets").envelope()
        assert envelope.ops[0].extras == {"title": "Wallets"}

    def test_absolute_bypasses_the_template(self) -> None:
        envelope = (
            Patches(partial_request("/titled/leaf/"))
            .meta("Wallets", absolute=True)
            .envelope()
        )
        assert envelope.ops[0].as_dict() == {"op": "meta", "title": "Wallets"}

    @pytest.mark.parametrize(
        "builder",
        [
            lambda: Patches.versioned("v1"),
            lambda: Patches(partial_request(origin=None)),
            lambda: Patches(partial_request("/_next/form/x/")),
        ],
        ids=["no_request", "no_origin", "foreign_origin"],
    )
    def test_a_builder_without_an_origin_page_sends_the_bare_title(
        self, builder: Callable[[], Patches]
    ) -> None:
        envelope = builder().meta("Wallets").envelope()
        assert envelope.ops[0].as_dict() == {"op": "meta", "title": "Wallets"}

    def test_a_lazy_title_is_evaluated_when_the_op_is_recorded(self) -> None:
        language = ["en"]
        title = lazy(lambda: f"Wallets ({language[0]})", str)()
        builder = Patches(partial_request("/titled/leaf/")).meta(title)
        language[0] = "de"
        payload = builder.envelope().ops[0].as_dict()
        assert payload == {"op": "meta", "title": "Wallets (en) · Site"}
        assert type(payload["title"]) is str

    def test_op_refuses_the_builtin_verb(self) -> None:
        with pytest.raises(BuiltinPatchOpError):
            Patches.versioned("v1").op("meta", title="Wallets")

    def test_an_inherited_callable_template_wraps_the_title(
        self, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(
            tmp_path, [("root", INHERITING_ROOT), ("leaf", LEAF_TITLE)]
        )
        with _routed(tmp_path):
            envelope = _builder_for(tmp_path, leaf).meta("Post").envelope()
        assert envelope.ops[0].extras == {"title": "Post | Kanban"}

    def test_an_inherited_callable_runs_behind_the_origin_guard(
        self, tmp_path: Path
    ) -> None:
        _root, leaf = write_page_chain(
            tmp_path, [("root", INHERITING_ROOT), ("leaf", DENYING_LEAF)]
        )
        with _routed(tmp_path), pytest.raises(ForeignPageNotAuthorizedError):
            _builder_for(tmp_path, leaf).meta("Post")

    def test_a_static_chain_runs_no_guard(self, tmp_path: Path) -> None:
        _root, leaf = write_page_chain(
            tmp_path, [("root", STATIC_ROOT), ("leaf", DENYING_LEAF)]
        )
        with _routed(tmp_path):
            envelope = _builder_for(tmp_path, leaf).meta("Post").envelope()
        assert envelope.ops[0].extras == {"title": "Post | Static"}

    def test_meta_chains_in_order(self) -> None:
        envelope = (
            Patches(partial_request("/titled/leaf/"))
            .push_url("/titled/leaf/")
            .meta("Wallets")
            .envelope()
        )
        assert [op.op for op in envelope.ops] == ["url", "meta"]


class TestPatchResponse:
    """`PatchResponse` is an HttpResponse carrying serialized bytes."""

    def test_default_content_type_and_body(self) -> None:
        response = PatchResponse(b'{"version":"v1"}')
        assert response["Content-Type"] == CONTENT_TYPE
        assert response.content == b'{"version":"v1"}'

    def test_version_header_set(self) -> None:
        response = PatchResponse(b"{}", version="9f3c")
        assert response["X-Next-Version"] == "9f3c"

    def test_vary_headers_stamped(self) -> None:
        response = PatchResponse(b"{}")
        assert "X-Next-Merge" in response["Vary"]

    def test_custom_status(self) -> None:
        response = PatchResponse(b"{}", status=409)
        assert response.status_code == 409


class TestBuilderExceptionSurface:
    """Demoted builder exceptions live only on the submodule, not the facade."""

    demoted_exceptions = (
        "BuiltinPatchOpError",
        "CrossSiteHrefError",
        "DynamicForeignPageError",
        "ReservedContextKeyError",
        "ReservedEventNameError",
        "ReservedPatchKeyError",
        "UnknownContextNameError",
        "UnknownDedupeError",
        "UnknownPatchOpError",
    )

    @pytest.mark.parametrize("name", demoted_exceptions)
    def test_demoted_exception_not_on_facade(self, name: str) -> None:
        assert name not in next.partial.__all__
        assert not hasattr(next.partial, name)
        assert isinstance(getattr(next.partial.errors, name), type)

    def test_foreign_page_error_stays_on_facade(self) -> None:
        assert "ForeignPageNotAuthorizedError" in next.partial.__all__
        assert (
            next.partial.ForeignPageNotAuthorizedError
            is next.partial.errors.ForeignPageNotAuthorizedError
        )


class TestReservedPatchKey:
    """A reserved structural key in a payload is refused at build time."""

    def test_op_frame_refuses_reserved_payload_key(self, custom_op: str) -> None:
        with pytest.raises(ReservedPatchKeyError) as exc:
            Patches.versioned("v1").op(custom_op, op="boom")
        assert exc.value.keys == frozenset({"op"})


class TestBuilderZoneManifest:
    """`morph_zone` ships the same inline and URL manifest as the view path."""

    def test_inline_and_url_assets_travel_together(self) -> None:
        envelope = (
            Patches(partial_request(origin="/zoned_inline/"))
            .morph(zone="styled")
            .envelope()
        )
        wire = [asset.as_dict() for asset in envelope.assets]
        assert {
            "kind": "css",
            "url": "",
            "inline": ".zone-styled { color: crimson; }",
            "load": "link",
        } in wire
        assert {
            "kind": "css",
            "url": "/static/next/zoned_inline.css",
            "load": "link",
        } in wire

    def test_inline_script_asset_travels(self) -> None:
        envelope = (
            Patches(partial_request(origin="/zoned_inline/"))
            .morph(zone="scripted")
            .envelope()
        )
        inline = [a.as_dict() for a in envelope.assets if a.url == ""]
        assert inline == [
            {
                "kind": "js",
                "url": "",
                "inline": 'console.log("zone scripted");',
                "load": "script",
            }
        ]

    def test_builder_path_emits_no_automatic_context_op(self) -> None:
        envelope = (
            Patches(partial_request(origin="/zoned_inline/"))
            .morph(zone="styled")
            .envelope()
        )
        assert [op.op for op in envelope.ops] == ["morph"]

    def test_explicit_context_still_rides_on_the_builder_path(self) -> None:
        envelope = (
            Patches(partial_request(origin="/zoned_inline/"))
            .morph(zone="styled")
            .context(seen=7)
            .envelope()
        )
        assert [op.op for op in envelope.ops] == ["morph", "context"]
        assert envelope.ops[1].as_dict() == {"op": "context", "data": {"seen": 7}}


class TestZoneOverridesReachTheMetadataTag:
    """A `{% metadata %}` in a zone body reads the overrides of the morph."""

    def test_the_override_replaces_the_context_value(self, tmp_path: Path) -> None:
        (leaf,) = write_page_chain(tmp_path, [("leaf", ZONED_LEAF)])
        (leaf.parent / "template.djx").write_text(
            '{% zone "head" %}{% metadata %}{% endzone %}'
        )
        with _routed(tmp_path):
            builder = _builder_for(tmp_path, leaf)
            bob = builder.morph(zone="head", overrides={"user": "Bob"}).envelope()
            assert "<title>Bob</title>" in bob.ops[0].html
            ann = builder.morph(zone="head").envelope()
        assert "<title>Ann</title>" in ann.ops[1].html


class TestZoneDeltaReservedKeys:
    """A zone js-context delta never patches a reserved init-payload key."""

    @staticmethod
    def _result(js_context: dict[str, object]) -> ZoneRenderResult:
        """Build a zone result whose collector carries the given js-context."""
        collector = default_manager.create_collector()
        for key, value in js_context.items():
            collector.add_js_context(key, value)
        return ZoneRenderResult(html={}, bodies={}, collector=collector)

    def test_reserved_key_is_dropped_from_the_delta(self) -> None:
        result = self._result({"$csrf": {"token": "forged"}, "unread": 3})
        envelope = Patches.versioned("v1").absorb_zone_result(result).envelope()
        assert envelope.ops[0].as_dict() == {"op": "context", "data": {"unread": 3}}

    def test_dev_key_is_dropped_too(self) -> None:
        result = self._result({"$dev": False, "unread": 3})
        envelope = Patches.versioned("v1").absorb_zone_result(result).envelope()
        assert envelope.ops[0].as_dict()["data"] == {"unread": 3}

    def test_only_reserved_keys_emit_no_context_op(self) -> None:
        result = self._result({"$csrf": {"token": "forged"}, "$dev": True})
        envelope = Patches.versioned("v1").absorb_zone_result(result).envelope()
        assert envelope.ops == ()

    def test_plain_delta_still_rides_out(self) -> None:
        result = self._result({"unread": 3})
        envelope = Patches.versioned("v1").absorb_zone_result(result).envelope()
        assert envelope.ops[0].as_dict() == {"op": "context", "data": {"unread": 3}}


class TestOriginRenderContextMemoised:
    """The origin render context is built once per builder."""

    def test_context_then_morph_builds_render_context_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0
        original = next.pages.page.build_render_context

        def _counting(*args, **kwargs) -> object:
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(next.pages.page, "build_render_context", _counting)
        Patches(partial_request()).context(flag=True).morph(zone="alpha").envelope()
        assert calls == 1

    def test_context_only_builds_render_context_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0
        original = next.pages.page.build_render_context

        def _counting(*args, **kwargs) -> object:
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(next.pages.page, "build_render_context", _counting)
        Patches(partial_request()).context(flag=True).envelope()
        assert calls == 1
