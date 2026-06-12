from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django import forms
from django.template import Context
from django.urls import clear_url_caches

from next.forms import Form
from next.forms.backends import ActionRegistration, RegistryFormActionBackend
from next.forms.manager import form_action_manager
from next.pages import page
from next.testing.isolation import reset_page_cache
from tests.support.helpers import build_mock_http_request


if TYPE_CHECKING:
    from pathlib import Path


class _ErrorForm(Form):
    title = forms.CharField()
    body = forms.CharField(widget=forms.Textarea)


_INVALID_PAGE_ACTION = "bench_invalid_page_action"
_TAG_ACTION = "bench_form_tag_action"
_LAYERED_ACTION = "bench_layered_rerender_action"
_PAGE_GET_ACTION = "bench_page_get_action"


def _build_layered_page(tmp_path: Path, template_body: str) -> Path:
    """Create page.py and template.djx behind two nested layout.djx wrappers."""
    (tmp_path / "layout.djx").write_text(
        "<html><body>{% block template %}{% endblock template %}</body></html>"
    )
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "layout.djx").write_text(
        "<section>{% block template %}{% endblock template %}</section>"
    )
    leaf = inner / "pageA"
    leaf.mkdir()
    page_file = leaf / "page.py"
    page_file.write_text("")
    (leaf / "template.djx").write_text(template_body)
    return page_file


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
            name=_INVALID_PAGE_ACTION,
            file_path=str(page_file),
            scope="page",
            form_class=_ErrorForm,
        )
    )
    return backend, page_file


class TestBenchRenderInvalidPage:
    """Validation-failure re-render through ``render_form_page_with_errors``."""

    @pytest.mark.benchmark(group="forms.rendering")
    def test_render_invalid_page_with_errors(
        self,
        error_render_setup: tuple[RegistryFormActionBackend, Path],
        benchmark,
    ) -> None:
        backend, page_file = error_render_setup
        request = build_mock_http_request(method="GET")
        form = _ErrorForm(data={"title": "", "body": ""})
        form.is_valid()

        def run() -> str:
            return backend.render_invalid_page(
                request,
                _INVALID_PAGE_ACTION,
                form,
                page_file_path=page_file,
            )

        benchmark(run)


class TestBenchErrorRerenderWithLayouts:
    """Validation re-render paying for the layout chain a real app pays for."""

    @pytest.fixture()
    def layered_error_setup(
        self, tmp_path: Path
    ) -> tuple[RegistryFormActionBackend, Path]:
        page_file = _build_layered_page(
            tmp_path,
            "<main>{{ form.title }}{{ form.body }}{{ form.errors }}</main>",
        )
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name=_LAYERED_ACTION,
                file_path=str(page_file),
                scope="page",
                form_class=_ErrorForm,
            )
        )
        return backend, page_file

    @pytest.mark.benchmark(group="forms.rendering.page")
    def test_error_rerender_with_layouts(
        self,
        layered_error_setup: tuple[RegistryFormActionBackend, Path],
        benchmark,
    ) -> None:
        backend, page_file = layered_error_setup
        request = build_mock_http_request(method="GET")
        form = _ErrorForm(data={"title": "", "body": ""})
        form.is_valid()

        def run() -> str:
            return backend.render_invalid_page(
                request,
                _LAYERED_ACTION,
                form,
                page_file_path=page_file,
            )

        benchmark(run)


class TestBenchPageGetRender:
    """GET render of a page whose template carries a `{% form %}` block."""

    @pytest.fixture()
    def form_page(self, tmp_path: Path) -> Path:
        page_file = _build_layered_page(
            tmp_path,
            "{% load forms %}"
            f'{{% form "{_PAGE_GET_ACTION}" %}}{{{{ form.title }}}}{{% endform %}}',
        )
        form_action_manager._ensure_backends()
        form_action_manager.register_action(
            ActionRegistration(
                name=_PAGE_GET_ACTION,
                file_path=str(page_file),
                scope="page",
                form_class=_ErrorForm,
            )
        )
        clear_url_caches()
        return page_file

    @pytest.mark.benchmark(group="forms.rendering.page")
    def test_page_render_warm_cache(self, form_page, csrf_request, benchmark) -> None:
        reset_page_cache()
        page.render(form_page, csrf_request)
        benchmark(page.render, form_page, csrf_request)

    @pytest.mark.benchmark(group="forms.rendering.page")
    def test_page_render_cold_cache(self, form_page, csrf_request, benchmark) -> None:
        def run() -> str:
            reset_page_cache()
            return page.render(form_page, csrf_request)

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
