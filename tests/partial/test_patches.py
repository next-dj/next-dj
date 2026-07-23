import pytest

import next.pages
import next.partial
import next.partial.patches
from next.partial import (
    Asset,
    Envelope,
    FormMeta,
    Patch,
    Patches,
    PatchResponse,
    register_patch_op,
)
from next.partial.headers import CONTENT_TYPE
from next.partial.patches import ReservedPatchKeyError
from next.partial.registry import patch_op_registry
from next.partial.render import ZoneRenderResult
from next.static import KindRegistry
from next.static.manager import default_manager
from tests.support import partial_request


class TestPatchAsDict:
    """A patch serialises to an ordered mapping with verb first."""

    def test_html_verb(self) -> None:
        patch = Patch(op="replace", target={"zone": "list"}, html="<div></div>")
        assert patch.as_dict() == {
            "op": "replace",
            "target": {"zone": "list"},
            "html": "<div></div>",
        }

    def test_remove_has_no_html(self) -> None:
        patch = Patch(op="remove", target={"zone": "list"})
        assert patch.as_dict() == {"op": "remove", "target": {"zone": "list"}}

    def test_event_carries_extras(self) -> None:
        patch = Patch(op="event", extras={"name": "ping", "detail": {"x": 1}})
        assert patch.as_dict() == {"op": "event", "name": "ping", "detail": {"x": 1}}


class TestAssetAsDict:
    """An asset serialises with its inline body only when one is present."""

    def test_url_form_asset_carries_no_inline_key(self) -> None:
        assert Asset(kind="css", url="/a.css").as_dict() == {
            "kind": "css",
            "url": "/a.css",
        }

    def test_inline_form_asset_carries_its_body(self) -> None:
        asset = Asset(kind="css", url="", inline=".x { color: red; }")
        assert asset.as_dict() == {
            "kind": "css",
            "url": "",
            "inline": ".x { color: red; }",
        }

    def test_load_travels_when_set(self) -> None:
        asset = Asset(kind="module", url="/a.mjs", load="module")
        assert asset.as_dict() == {"kind": "module", "url": "/a.mjs", "load": "module"}


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


class TestEnvelopeAsDict:
    """The envelope wire form carries ops and meta with stable keys."""

    def test_minimal_envelope(self) -> None:
        envelope = Envelope(version="v1")
        assert envelope.as_dict() == {
            "version": "v1",
            "ops": [],
            "assets": [],
            "form": None,
        }

    def test_full_envelope(self) -> None:
        envelope = Envelope(
            version="v1",
            ops=(Patch(op="remove", target={"zone": "row"}),),
            assets=(Asset(kind="css", url="/a.css"),),
            form=FormMeta(uid="ab12", valid=False, errors={"name": ["required"]}),
        )
        data = envelope.as_dict()
        assert data["ops"] == [{"op": "remove", "target": {"zone": "row"}}]
        assert data["assets"] == [{"kind": "css", "url": "/a.css"}]
        assert "defer" not in data
        assert data["form"] == {
            "uid": "ab12",
            "valid": False,
            "errors": {"name": ["required"]},
        }

    def test_csrf_and_request_id_present_only_when_set(self) -> None:
        envelope = Envelope(version="v1", csrf={"token": "t"}, request_id="r1")
        data = envelope.as_dict()
        assert data["csrf"] == {"token": "t"}
        assert data["request_id"] == "r1"

    def test_csrf_and_request_id_omitted_by_default(self) -> None:
        data = Envelope(version="v1").as_dict()
        assert "csrf" not in data
        assert "request_id" not in data


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

    def test_morph_extract_marks_payload(self) -> None:
        envelope = (
            Patches.versioned("v1")
            .morph({"form": "ab12"}, "<html></html>", extract=True)
            .envelope()
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
        with pytest.raises(TypeError, match="unexpected selector"):
            Patches.versioned("v1").morph(widget="ab12")

    def test_morph_rejects_conflicting_selectors(self) -> None:
        with pytest.raises(TypeError, match="conflicting selector"):
            Patches.versioned("v1").morph(zone="list", form="ab12")

    def test_morph_rejects_a_none_valued_selector(self) -> None:
        with pytest.raises(TypeError, match="unexpected selector"):
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
        assert isinstance(getattr(next.partial.patches, name), type)

    def test_foreign_page_error_stays_on_facade(self) -> None:
        assert "ForeignPageNotAuthorizedError" in next.partial.__all__
        assert (
            next.partial.ForeignPageNotAuthorizedError
            is next.partial.patches.ForeignPageNotAuthorizedError
        )


@pytest.fixture()
def custom_op():
    """Register a custom patch verb for the test and drop it afterwards."""
    register_patch_op("confetti")
    yield "confetti"
    patch_op_registry._ops.discard("confetti")
    patch_op_registry._custom.discard("confetti")


class TestReservedPatchKey:
    """A reserved structural key in a payload is refused at build time."""

    def test_constructor_refuses_reserved_extras_key(self) -> None:
        with pytest.raises(ReservedPatchKeyError) as exc:
            Patch(op="x", extras={"target": 1})
        assert exc.value.keys == frozenset({"target"})

    def test_op_frame_refuses_reserved_payload_key(self, custom_op: str) -> None:
        with pytest.raises(ReservedPatchKeyError) as exc:
            Patches.versioned("v1").op(custom_op, op="boom")
        assert exc.value.keys == frozenset({"op"})

    def test_valid_patch_as_dict_does_not_raise(self) -> None:
        patch = Patch(op="event", extras={"name": "ping"})
        assert patch.as_dict() == {"op": "event", "name": "ping"}

    def test_multiple_reserved_keys_are_sorted_in_message(self) -> None:
        with pytest.raises(ReservedPatchKeyError) as exc:
            Patch(op="x", extras={"target": 1, "op": 2, "html": 3})
        assert exc.value.keys == frozenset({"op", "target", "html"})
        assert "html, op, target" in str(exc.value)


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
        envelope = Patches.versioned("v1")._absorb_zone_result(result).envelope()
        assert envelope.ops[0].as_dict() == {"op": "context", "data": {"unread": 3}}

    def test_dev_key_is_dropped_too(self) -> None:
        result = self._result({"$dev": False, "unread": 3})
        envelope = Patches.versioned("v1")._absorb_zone_result(result).envelope()
        assert envelope.ops[0].as_dict()["data"] == {"unread": 3}

    def test_only_reserved_keys_emit_no_context_op(self) -> None:
        result = self._result({"$csrf": {"token": "forged"}, "$dev": True})
        envelope = Patches.versioned("v1")._absorb_zone_result(result).envelope()
        assert envelope.ops == ()

    def test_plain_delta_still_rides_out(self) -> None:
        result = self._result({"unread": 3})
        envelope = Patches.versioned("v1")._absorb_zone_result(result).envelope()
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
