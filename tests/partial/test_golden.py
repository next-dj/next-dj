import json

import pytest

from next.partial import Envelope, FormMeta, Patch, Patches
from tests.partial.golden_support import (
    GoldenCase,
    read_envelope_bytes,
    read_meta,
    write_case,
)


def _replace_zone() -> GoldenCase:
    html = '<div data-next-zone="request-list"><ul></ul></div>'
    envelope = Patches("9f3c2e1b").replace({"zone": "request-list"}, html).envelope()
    return GoldenCase(
        name="replace_zone",
        envelope=envelope,
        description="A single replace patch swapping a named zone wholesale.",
        version="9f3c2e1b",
    )


def _inner_zone() -> GoldenCase:
    html = "<li>one</li><li>two</li>"
    envelope = Patches("9f3c2e1b").inner({"zone": "request-list"}, html).envelope()
    return GoldenCase(
        name="inner_zone",
        envelope=envelope,
        description="An inner patch replacing only the contents of a zone.",
        version="9f3c2e1b",
    )


def _remove_row() -> GoldenCase:
    envelope = Patches("9f3c2e1b").remove({"css": "#row-42"}).envelope()
    return GoldenCase(
        name="remove_row",
        envelope=envelope,
        description="A remove patch deleting a target addressed by selector.",
        version="9f3c2e1b",
    )


def _event_only() -> GoldenCase:
    envelope = Patches("9f3c2e1b").event("request-created", {"id": 42}).envelope()
    return GoldenCase(
        name="event_only",
        envelope=envelope,
        description="An HTML-less event patch dispatching a CustomEvent.",
        version="9f3c2e1b",
    )


def _zone_get() -> GoldenCase:
    alpha = '<div data-next-zone="alpha"><p>alpha hi</p></div>'
    beta = '<section data-next-zone="beta"><p>beta hi</p></section>'
    envelope = (
        Patches("9f3c2e1b")
        .morph({"zone": "alpha"}, alpha)
        .morph({"zone": "beta"}, beta)
        .add_asset("css", "/static/next/zoned.css")
        .envelope()
    )
    return GoldenCase(
        name="zone_get",
        envelope=envelope,
        description="A batched zone GET morphing two zones with an asset manifest.",
        version="9f3c2e1b",
    )


def _invalid_form() -> GoldenCase:
    html = (
        '<form data-next-action="ab12cd34">'
        '<ul class="errorlist"><li>This field is required.</li></ul>'
        '<input name="name" value="" aria-invalid="true"></form>'
    )
    form = FormMeta(
        uid="ab12cd34",
        valid=False,
        errors={"name": ["This field is required."]},
    )
    envelope = (
        Patches("9f3c2e1b")
        .replace({"form": "ab12cd34"}, html)
        .event("toast", {"text": "Could not save", "variant": "error"})
        .set_form(form)
        .envelope()
    )
    return GoldenCase(
        name="invalid_form",
        envelope=envelope,
        description="A form morph with machine-readable errors and a toast event.",
        version="9f3c2e1b",
        extra_headers={"X-Next-Form": "invalid", "X-Next-Action": "ab12cd34"},
    )


def _invalid_form_extract() -> GoldenCase:
    html = (
        "<!doctype html><html><body>"
        '<form data-next-action="3f9ac21d75e04b88">'
        '<ul class="errorlist"><li>This field is required.</li></ul>'
        '<input name="title" value="" aria-invalid="true"></form>'
        "</body></html>"
    )
    form = FormMeta(
        uid="3f9ac21d75e04b88",
        valid=False,
        errors={"title": ["This field is required."]},
    )
    envelope = (
        Patches("9f3c2e1b")
        .morph({"form": "3f9ac21d75e04b88"}, html, extract=True)
        .set_form(form)
        .envelope()
    )
    return GoldenCase(
        name="invalid_form_extract",
        envelope=envelope,
        description=(
            "The default invalid-form envelope: one extract-morph addressing "
            "the failed form by uid, the document trimmed to it by the client."
        ),
        version="9f3c2e1b",
        extra_headers={
            "X-Next-Form": "invalid",
            "X-Next-Action": "3f9ac21d75e04b88",
        },
    )


def _validate_form() -> GoldenCase:
    html = (
        '<form data-next-action="ab12cd34" data-next-validate="blur">'
        '<ul class="errorlist"><li>Enter a valid email address.</li></ul>'
        '<input name="email" value="bad" aria-invalid="true"></form>'
    )
    form = FormMeta(
        uid="ab12cd34",
        valid=False,
        errors={"email": ["Enter a valid email address."]},
    )
    envelope = (
        Patches("9f3c2e1b")
        .morph({"form": "ab12cd34"}, html, extract=True)
        .set_form(form)
        .envelope()
    )
    return GoldenCase(
        name="validate_form",
        envelope=envelope,
        description=(
            "An inline validate pass: one form morph by uid carrying only the "
            "blurred field's error, no invalid-submission headers."
        ),
        version="9f3c2e1b",
    )


def _wizard_advance() -> GoldenCase:
    html = (
        '<div data-next-zone="wizard-zone">'
        '<form data-next-action="ab12cd34">'
        '<input name="scope" value=""></form></div>'
    )
    envelope = Patches("9f3c2e1b").morph({"zone": "wizard-zone"}, html).envelope()
    return GoldenCase(
        name="wizard_advance",
        envelope=envelope,
        description=(
            "A wizard step advance: one zone morph swapping the master zone "
            "for the next step's unbound form, never a redirect."
        ),
        version="9f3c2e1b",
    )


def _layer_close() -> GoldenCase:
    envelope = (
        Patches("9f3c2e1b")
        .layer_close(result={"id": 42})
        .toast("Request created", variant="success")
        .envelope()
    )
    return GoldenCase(
        name="layer_close",
        envelope=envelope,
        description=(
            "The default wizard done: close the layer with a result and toast, "
            "leaving the host re-GET to the client by data-next-accepted."
        ),
        version="9f3c2e1b",
    )


def _layer_oob_list() -> GoldenCase:
    html = '<div data-next-zone="request-list"><ul><li>fresh</li></ul></div>'
    envelope = (
        Patches("9f3c2e1b")
        .layer_close(result={"id": 42})
        .morph({"zone": "request-list"}, html)
        .toast("Request created", variant="success")
        .envelope()
    )
    return GoldenCase(
        name="layer_oob_list",
        envelope=envelope,
        description=(
            "The page=-OOB done: one envelope closing the layer, morphing the "
            "host page's foreign zone, and toasting, applied in a single pass."
        ),
        version="9f3c2e1b",
    )


def _append_page() -> GoldenCase:
    rows = (
        '<li data-next-key="11">eleven</li>'
        '<li data-next-key="12">twelve</li>'
        '<li data-next-key="sentinel" id="sentinel">'
        '<a href="/catalog/?page=3" data-next-merge="append" '
        'data-next-target="catalog-results">more</a></li>'
    )
    envelope = (
        Patches("9f3c2e1b").append({"zone": "catalog-results"}, rows).envelope()
    )
    return GoldenCase(
        name="append_page",
        envelope=envelope,
        description=(
            "A paginating zone GET under X-Next-Merge: one append patch growing "
            "the list with key-deduplicated rows and a fresh infinite-scroll "
            "sentinel that replaces the old one by id."
        ),
        version="9f3c2e1b",
    )


def _defer_zone() -> GoldenCase:
    html = '<div data-next-zone="summary"><p>saved</p></div>'
    envelope = (
        Patches("9f3c2e1b")
        .morph({"zone": "summary"}, html)
        .defer_zone("audit-table")
        .envelope()
    )
    return GoldenCase(
        name="defer_zone",
        envelope=envelope,
        description=(
            "A form response that morphs its own zone and defers an audit zone: "
            "the runtime queues the deferred zone for a follow-up load GET."
        ),
        version="9f3c2e1b",
    )


def _result_form_visit() -> GoldenCase:
    envelope = Envelope(
        version="9f3c2e1b",
        ops=(Patch(op="visit", extras={"href": "/board/7/settings/"}),),
    )
    return GoldenCase(
        name="result_form_visit",
        envelope=envelope,
        description=(
            "A successful submission whose handler redirected: the redirect is "
            "packed into one internal visit the client navigates to."
        ),
        version="9f3c2e1b",
    )


GOLDEN_CASES = [
    _replace_zone(),
    _inner_zone(),
    _remove_row(),
    _event_only(),
    _zone_get(),
    _invalid_form(),
    _invalid_form_extract(),
    _validate_form(),
    _wizard_advance(),
    _layer_close(),
    _layer_oob_list(),
    _append_page(),
    _defer_zone(),
    _result_form_visit(),
]


class TestWriteGoldenFixtures:
    """pytest writes real envelopes for vitest to read back through the applier."""

    @pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.name)
    def test_writes_envelope_and_meta(self, case: GoldenCase) -> None:
        envelope_path, meta_path = write_case(case)
        assert envelope_path.exists()
        assert meta_path.exists()

    @pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.name)
    def test_envelope_bytes_are_valid_json(self, case: GoldenCase) -> None:
        write_case(case)
        data = json.loads(read_envelope_bytes(case.name))
        assert data["version"] == "9f3c2e1b"
        assert isinstance(data["ops"], list)

    @pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.name)
    def test_meta_declares_content_type(self, case: GoldenCase) -> None:
        write_case(case)
        meta = read_meta(case.name)
        assert meta["content_type"] == "application/vnd.next.patches+json"
        assert meta["status"] == case.status

    @pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.name)
    def test_meta_carries_response_headers(self, case: GoldenCase) -> None:
        write_case(case)
        headers = read_meta(case.name)["headers"]
        assert isinstance(headers, dict)
        assert headers["Content-Type"] == "application/vnd.next.patches+json"
        assert "X-Next-Merge" in headers["Vary"]
        assert headers["X-Next-Version"] == "9f3c2e1b"

    @pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.name)
    def test_meta_points_at_envelope_file(self, case: GoldenCase) -> None:
        write_case(case)
        meta = read_meta(case.name)
        assert meta["envelope_file"] == f"{case.name}.envelope.json"

    def test_invalid_form_meta_headers_present(self) -> None:
        write_case(_invalid_form())
        headers = read_meta("invalid_form")["headers"]
        assert headers["X-Next-Form"] == "invalid"
        assert headers["X-Next-Action"] == "ab12cd34"

    def test_invalid_form_envelope_carries_form_meta(self) -> None:
        write_case(_invalid_form())
        data = json.loads(read_envelope_bytes("invalid_form"))
        assert data["form"] == {
            "uid": "ab12cd34",
            "valid": False,
            "errors": {"name": ["This field is required."]},
        }

    def test_zone_get_envelope_morphs_both_zones(self) -> None:
        write_case(_zone_get())
        data = json.loads(read_envelope_bytes("zone_get"))
        targets = [op["target"]["zone"] for op in data["ops"]]
        assert targets == ["alpha", "beta"]

    def test_zone_get_envelope_carries_asset_manifest(self) -> None:
        write_case(_zone_get())
        data = json.loads(read_envelope_bytes("zone_get"))
        assert data["assets"] == [{"kind": "css", "url": "/static/next/zoned.css"}]

    def test_invalid_extract_envelope_marks_extract_on_the_form_target(self) -> None:
        write_case(_invalid_form_extract())
        data = json.loads(read_envelope_bytes("invalid_form_extract"))
        op = data["ops"][0]
        assert op["op"] == "morph"
        assert op["target"] == {"form": "3f9ac21d75e04b88"}
        assert op["extract"] is True

    def test_invalid_extract_meta_headers_present(self) -> None:
        write_case(_invalid_form_extract())
        headers = read_meta("invalid_form_extract")["headers"]
        assert headers["X-Next-Form"] == "invalid"
        assert headers["X-Next-Action"] == "3f9ac21d75e04b88"

    def test_result_form_visit_envelope_carries_one_internal_visit(self) -> None:
        write_case(_result_form_visit())
        data = json.loads(read_envelope_bytes("result_form_visit"))
        assert data["ops"] == [{"op": "visit", "href": "/board/7/settings/"}]

    def test_validate_envelope_morphs_the_form_with_meta(self) -> None:
        write_case(_validate_form())
        data = json.loads(read_envelope_bytes("validate_form"))
        op = data["ops"][0]
        assert op["op"] == "morph"
        assert op["target"] == {"form": "ab12cd34"}
        assert data["form"]["errors"] == {"email": ["Enter a valid email address."]}

    def test_validate_meta_omits_the_invalid_headers(self) -> None:
        write_case(_validate_form())
        headers = read_meta("validate_form")["headers"]
        assert "X-Next-Form" not in headers
        assert "X-Next-Action" not in headers

    def test_wizard_advance_envelope_morphs_the_master_zone(self) -> None:
        write_case(_wizard_advance())
        data = json.loads(read_envelope_bytes("wizard_advance"))
        assert [op["op"] for op in data["ops"]] == ["morph"]
        assert data["ops"][0]["target"] == {"zone": "wizard-zone"}
        assert 'name="scope"' in data["ops"][0]["html"]

    def test_layer_close_envelope_closes_with_a_result_and_toasts(self) -> None:
        write_case(_layer_close())
        data = json.loads(read_envelope_bytes("layer_close"))
        assert [op["op"] for op in data["ops"]] == ["layer.close", "toast"]
        assert data["ops"][0]["result"] == {"id": 42}
        assert data["ops"][1]["text"] == "Request created"
        assert data["ops"][1]["variant"] == "success"

    def test_layer_oob_envelope_closes_morphs_the_host_zone_and_toasts(self) -> None:
        write_case(_layer_oob_list())
        data = json.loads(read_envelope_bytes("layer_oob_list"))
        assert [op["op"] for op in data["ops"]] == ["layer.close", "morph", "toast"]
        assert data["ops"][1]["target"] == {"zone": "request-list"}
        assert "fresh" in data["ops"][1]["html"]

    def test_append_page_envelope_grows_the_zone_with_keyed_rows(self) -> None:
        write_case(_append_page())
        data = json.loads(read_envelope_bytes("append_page"))
        op = data["ops"][0]
        assert op["op"] == "append"
        assert op["target"] == {"zone": "catalog-results"}
        assert op["dedupe"] == "key"
        assert 'data-next-key="11"' in op["html"]
        assert 'id="sentinel"' in op["html"]

    def test_defer_zone_envelope_queues_the_audit_zone(self) -> None:
        write_case(_defer_zone())
        data = json.loads(read_envelope_bytes("defer_zone"))
        assert [op["op"] for op in data["ops"]] == ["morph"]
        assert data["defer"] == [{"zone": "audit-table", "trigger": "load"}]
