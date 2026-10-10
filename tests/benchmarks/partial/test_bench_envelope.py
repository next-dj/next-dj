from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django import forms
from django.test import RequestFactory

from next.forms import Form
from next.forms.backends import ActionRegistration, RegistryFormActionBackend
from next.forms.dispatch.responses import ActionOutcome, ActionOutcomeKind
from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial import JsonPartialProtocolBackend, Patches, shape_partial
from next.partial.headers import REQUEST_FLAG
from tests.support import partial_request


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
        protocol = JsonPartialProtocolBackend()

        def run() -> bytes:
            envelope = (
                Patches.versioned("9f3c2e1b")
                .morph({"zone": "results"}, "<div>results</div>")
                .append({"zone": "feed"}, "<li>row</li>")
                .toast("Saved", variant="success")
                .event("saved", {"id": 7})
                .replace({"form": "ab12"}, "<form></form>")
                .envelope()
            )
            return protocol.serialize_envelope(envelope)

        benchmark(run)

    @pytest.mark.benchmark(group="partial.envelope")
    def test_meta_over_the_site_defaults(self, benchmark) -> None:
        """A head update with no origin page, folded over the site defaults."""
        metadata = {"title": "Saved", "description": "The draft is saved."}
        benchmark(lambda: Patches.versioned("9f3c2e1b").meta(metadata).envelope())

    @pytest.mark.benchmark(group="partial.envelope")
    def test_meta_over_an_origin_page(self, benchmark) -> None:
        """A head update folded over the chain of the page the action was posted from.

        Each round takes a fresh request, so the origin resolves as it does per action.
        """
        metadata = {"title": "Saved", "description": "The draft is saved."}
        Patches(partial_request("/titled/leaf/")).meta(metadata).envelope()

        def setup() -> tuple[tuple[Patches], dict[str, object]]:
            return (Patches(partial_request("/titled/leaf/")),), {}

        benchmark.pedantic(
            lambda builder: builder.meta(metadata).envelope(),
            setup=setup,
            rounds=200,
            warmup_rounds=5,
        )


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
        self, invalid_setup: tuple[RegistryFormActionBackend, ActionOutcome], benchmark
    ) -> None:
        backend, outcome = invalid_setup
        request = RequestFactory().post(
            "/_next/form/x/", data={ORIGIN_FIELD_NAME: "/"}, **_PARTIAL_META
        )

        def run() -> object:
            return shape_partial(backend, request, outcome)

        benchmark(run)
