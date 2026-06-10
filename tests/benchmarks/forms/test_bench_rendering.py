from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django import forms
from django.template import Context
from django.urls import clear_url_caches

from next.forms import Form
from next.forms.backends import ActionRegistration, RegistryFormActionBackend
from next.forms.manager import form_action_manager
from tests.support.helpers import build_mock_http_request


if TYPE_CHECKING:
    from pathlib import Path


class _ErrorForm(Form):
    title = forms.CharField()
    body = forms.CharField(widget=forms.Textarea)


_FRAGMENT_ACTION = "bench_fragment_action"
_TAG_ACTION = "bench_form_tag_action"


@pytest.fixture()
def error_render_setup(tmp_path: Path) -> tuple[RegistryFormActionBackend, Path]:
    """Page-scoped action with a real page.py/template.djx pair under tmp_path."""
    page_file = tmp_path / "page.py"
    page_file.write_text("")
    (tmp_path / "template.djx").write_text(
        "<main>{{ form.title }}{{ form.body }}{{ form.errors }}</main>"
    )
    backend = RegistryFormActionBackend()
    backend.register_action(
        ActionRegistration(
            name=_FRAGMENT_ACTION,
            file_path=str(page_file),
            scope="page",
            form_class=_ErrorForm,
        )
    )
    return backend, page_file


class TestBenchRenderFormFragment:
    """Validation-failure re-render through ``render_form_page_with_errors``."""

    @pytest.mark.benchmark(group="forms.rendering")
    def test_render_form_fragment_with_errors(
        self,
        error_render_setup: tuple[RegistryFormActionBackend, Path],
        benchmark,
    ) -> None:
        backend, page_file = error_render_setup
        request = build_mock_http_request(method="GET")
        form = _ErrorForm(data={"title": "", "body": ""})
        form.is_valid()

        def run() -> str:
            return backend.render_form_fragment(
                request,
                _FRAGMENT_ACTION,
                form,
                page_file_path=page_file,
            )

        benchmark(run)


class TestBenchFormTag:
    """Full ``{% form %}`` block render: URL reverse, hidden inputs, namespace."""

    @pytest.fixture(autouse=True)
    def _register_tag_action(self) -> None:
        form_action_manager._ensure_backends()
        form_action_manager.register_action(
            ActionRegistration(
                name=_TAG_ACTION,
                file_path=__file__,
                scope="page",
                form_class=_ErrorForm,
            )
        )
        clear_url_caches()

    @pytest.mark.benchmark(group="forms.rendering")
    def test_form_tag_full_render(self, form_engine, csrf_request, benchmark) -> None:
        template = form_engine.from_string(
            '{% form "bench_form_tag_action" %}{{ form.as_div }}{% endform %}'
        )

        def run() -> str:
            return template.render(
                Context(
                    {
                        "request": csrf_request,
                        "current_page_module_path": __file__,
                    }
                )
            )

        benchmark(run)
