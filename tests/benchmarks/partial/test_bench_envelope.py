from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django import forms
from django.test import RequestFactory

from next.forms import Form
from next.forms.backends import ActionRegistration, RegistryFormActionBackend
from next.forms.dispatch import ActionOutcome, ActionOutcomeKind
from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial import PartialProtocolBackend, Patches, shape_partial
from next.partial.headers import REQUEST_FLAG


if TYPE_CHECKING:
    from pathlib import Path


_PARTIAL_META = {f"HTTP_{REQUEST_FLAG.upper().replace('-', '_')}": "1"}
_INVALID_ACTION = "bench_partial_invalid_action"


class _RenameForm(Form):
    title = forms.CharField(max_length=100)


class TestBenchEnvelopeBuild:
    """Build a multi-op envelope and serialise it to bytes."""

    @pytest.mark.benchmark(group="partial.envelope")
    def test_build_and_serialise(self, benchmark) -> None:
        protocol = PartialProtocolBackend()

        def run() -> bytes:
            envelope = (
                Patches("9f3c2e1b")
                .morph({"zone": "results"}, "<div>results</div>")
                .append({"zone": "feed"}, "<li>row</li>")
                .toast("Saved", variant="success")
                .event("saved", {"id": 7})
                .replace({"form": "ab12"}, "<form></form>")
                .envelope()
            )
            return protocol.serialize_envelope(envelope)

        benchmark(run)


class TestBenchShapeInvalid:
    """Shape an INVALID outcome into a patch envelope through the form path."""

    @pytest.fixture()
    def invalid_setup(
        self, tmp_path: Path
    ) -> tuple[RegistryFormActionBackend, ActionOutcome]:
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        (tmp_path / "template.djx").write_text(
            "<main>{{ form.title }}{{ form.title.errors }}</main>"
        )
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name=_INVALID_ACTION,
                file_path=str(page_file),
                scope="page",
                form_class=_RenameForm,
            )
        )
        form = _RenameForm(data={"title": ""})
        form.is_valid()
        uid = str(backend.get_meta(_INVALID_ACTION)["uid"])
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.INVALID,
            action_name=_INVALID_ACTION,
            uid=uid,
            form=form,
            url_kwargs={},
            page_path=page_file,
        )
        return backend, outcome

    @pytest.mark.benchmark(group="partial.shaping")
    def test_shape_invalid_outcome(
        self,
        invalid_setup: tuple[RegistryFormActionBackend, ActionOutcome],
        benchmark,
    ) -> None:
        backend, outcome = invalid_setup
        request = RequestFactory().post(
            "/_next/form/x/", data={ORIGIN_FIELD_NAME: "/"}, **_PARTIAL_META
        )

        def run() -> object:
            return shape_partial(backend, request, outcome)

        benchmark(run)
