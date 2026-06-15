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
