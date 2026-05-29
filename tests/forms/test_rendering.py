import types
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from django import forms as django_forms
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.template import Context, TemplateSyntaxError

from next.forms import (
    Form,
    RegistryFormActionBackend,
    form_action_manager,
)
from next.forms.rendering import render_form_page_with_errors
from next.forms.wizard import FormWizard
from tests.forms.actions import SimpleForm


PAGE_MODULE_FOR_FORM_TESTS = (
    Path(__file__).resolve().parent.parent / "site_pages" / "page.py"
).resolve()


class RenderWizardStep(Form):
    """Step form for the form-tag wizard render test."""

    name = django_forms.CharField(max_length=100)


class RenderWizard(FormWizard):
    """Wizard exercised through the {% form %} template tag."""

    class Meta:
        """One step routed through the wizard backend."""

        steps: ClassVar = [("identity", RenderWizardStep)]

    def done(self, request, cleaned_data) -> HttpResponseRedirect:
        """Redirect once the wizard finishes."""
        return HttpResponseRedirect("/thanks/")


class TestRenderFormFragment:
    """render_form_fragment: unknown action, page template, fallback, context."""

    def test_unknown_action_returns_empty(self, mock_http_request) -> None:
        """Unknown action renders empty string."""
        request = mock_http_request(method="GET")
        html = form_action_manager.render_form_fragment(
            request, "unknown_action_xyz", None
        )
        assert html == ""

    def test_renders_with_page_template(self, mock_http_request) -> None:
        """Render form using the page template for ``page_file_path``."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "test"})
        html = form_action_manager.render_form_fragment(
            request,
            "simple_form",
            form,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert "test" in html or "name" in html

    def test_with_form_only_no_template(self, mock_http_request) -> None:
        """Render form via registry template for ``page_file_path`` returns HTML."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "x"})
        html = form_action_manager.render_form_fragment(
            request,
            "simple_form",
            form,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert isinstance(html, str)
        assert html.strip() != ""

    def test_form_none_no_template_returns_string(self, mock_http_request) -> None:
        """Form None still returns a string when a page template exists."""
        request = mock_http_request(method="GET")
        html = form_action_manager.render_form_fragment(
            request,
            "simple_form",
            form=None,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert isinstance(html, str)

    def test_unknown_action_with_form_renders_fallback(self, mock_http_request) -> None:
        """An unknown action with a bound form falls back to the form's own render."""
        request = mock_http_request(method="GET")
        backend = form_action_manager.default_backend
        form = SimpleForm(initial={"name": "fallback"})
        html = render_form_page_with_errors(
            backend,
            request,
            "unknown_action_xyz",
            form,
            PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert "name" in html

    def test_unknown_action_without_form_returns_empty(self, mock_http_request) -> None:
        """An unknown action with no form falls back to an empty string."""
        request = mock_http_request(method="GET")
        backend = form_action_manager.default_backend
        html = render_form_page_with_errors(
            backend,
            request,
            "unknown_action_xyz",
            None,
            PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert html == ""

    @pytest.mark.parametrize(
        ("template_body", "output_mode"),
        [
            ("{{ form.name }}", "form_fields"),
            ("{{ current_template_path }}", "path"),
        ],
        ids=("form_fields", "current_template_path"),
    )
    def test_renders_from_template_djx(
        self,
        mock_http_request,
        tmp_path,
        template_body: str,
        output_mode: str,
    ) -> None:
        """Render fragment reading the sibling template.djx of ``page_file_path``."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "a"})
        backend = form_action_manager.default_backend
        assert isinstance(backend, RegistryFormActionBackend)

        page_file = tmp_path / "page.py"
        page_file.write_text("")
        template_djx = tmp_path / "template.djx"
        template_djx.write_text(template_body)

        html = backend.render_form_fragment(
            request,
            "simple_form",
            form,
            page_file_path=page_file,
        )
        if output_mode == "path":
            assert str(template_djx) in html
        else:
            assert "name" in html


class TestFormTagSyntax:
    """{% form %} tag: required action name, syntax errors."""

    def test_requires_at_least_one_arg(self, form_engine) -> None:
        """{% form %} without args raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError):
            form_engine.from_string("{% form %}x{% endform %}")

    def test_too_many_args_raises(self, form_engine) -> None:
        """{% form %} with two args raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError):
            form_engine.from_string('{% form "foo" "bar" %}x{% endform %}')


class TestFormTagRender:
    """{% form %} tag: attributes, CSRF, unknown action, no request."""

    def test_renders_attributes(self, form_engine, csrf_request) -> None:
        """Form tag renders action, method, form content."""
        t = form_engine.from_string(
            '{% form "simple_form" %}{{ form.as_div }}{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "<form" in html
        assert "action=" in html
        assert 'method="post"' in html
        assert "</form>" in html

    def test_wizard_action_pushes_wizard_into_context(
        self, form_engine, csrf_request
    ) -> None:
        """A wizard action exposes the wizard alongside the current step form."""
        t = form_engine.from_string(
            '{% form "render_wizard" %}'
            "{{ wizard.current_step }}{{ form.as_div }}"
            "{% endform %}"
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "identity" in html
        assert 'name="name"' in html

    def test_resolves_action_from_context_variable(
        self, form_engine, csrf_request
    ) -> None:
        """Unquoted action name resolves against the template context at render time."""
        t = form_engine.from_string("{% form action_key %}x{% endform %}")
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                    "action_key": "simple_form",
                }
            )
        )
        assert "action_key" not in html
        assert 'action=""' not in html
        assert "/_next/form/" in html

    def test_includes_csrf_when_request_in_context(
        self, form_engine, csrf_request
    ) -> None:
        """Form includes csrfmiddlewaretoken when request in context."""
        t = form_engine.from_string('{% form "simple_form" %}x{% endform %}')
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "csrfmiddlewaretoken" in html

    def test_includes_next_form_page_hidden(self, form_engine, csrf_request) -> None:
        """Form includes _next_form_page from current_page_module_path."""
        t = form_engine.from_string('{% form "simple_form" %}x{% endform %}')
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "_next_form_page" in html
        assert str(PAGE_MODULE_FOR_FORM_TESTS) in html

    def test_includes_next_form_origin_hidden(self, form_engine, csrf_request) -> None:
        """Form emits `_next_form_origin` hidden field set to `request.path`."""
        csrf_request.path = "/admin/library/book/1/change/"
        t = form_engine.from_string('{% form "simple_form" %}x{% endform %}')
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "_next_form_origin" in html
        assert "/admin/library/book/1/change/" in html

    def test_unknown_action_raises_runtime_error(
        self, form_engine, csrf_request
    ) -> None:
        """Unknown action raises RuntimeError at render time."""
        t = form_engine.from_string('{% form "nonexistent_action_xyz" %}z{% endform %}')
        with pytest.raises(RuntimeError, match="Unknown form action"):
            t.render(
                Context(
                    {
                        "request": csrf_request,
                        "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                    }
                )
            )

    def test_without_request_in_context_raises(self, form_engine) -> None:
        """Form without request in context raises ImproperlyConfigured."""
        t = form_engine.from_string('{% form "simple_form" %}x{% endform %}')
        with pytest.raises(ImproperlyConfigured, match=r"request.*in template context"):
            t.render(Context({}))

    def test_form_variable_is_local(self, form_engine, csrf_request) -> None:
        """Form variable is only available inside {% form %} tag."""
        form_instance = SimpleForm(initial={"name": "test"})
        t = form_engine.from_string(
            'Outside: {{ form|default:"none" }} '
            '{% form "simple_form" %}Inside: {{ form.name.value|default:"none" }}{% endform %} '
            'Outside: {{ form|default:"none" }}'
        )
        context = Context(
            {
                "request": csrf_request,
                "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                "simple_form": types.SimpleNamespace(form=form_instance),
            }
        )
        html = t.render(context)
        assert "test" in html
        assert "Inside:" in html
        assert html.count("none") == 2

    def test_form_variable_contains_form_instance(
        self, form_engine, csrf_request
    ) -> None:
        """Form variable contains the actual form instance from context."""
        form_instance = SimpleForm(initial={"name": "test_name"})
        context = Context(
            {
                "request": csrf_request,
                "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                "simple_form": types.SimpleNamespace(form=form_instance),
            }
        )
        t = form_engine.from_string(
            '{% form "simple_form" %}{{ form.name.value }}{% endform %}'
        )
        html = t.render(context)
        assert "test_name" in html

    def test_form_includes_url_parameters_as_hidden_fields(
        self, form_engine, csrf_request
    ) -> None:
        """Form includes hidden fields for URL parameters from resolver_match."""
        t = form_engine.from_string('{% form "simple_form" %}x{% endform %}')

        request = HttpRequest()
        request.method = "GET"
        get_token(request)

        mock_resolver_match = MagicMock()
        mock_resolver_match.kwargs = {
            "id": 123,
            "slug": "test-slug",
            "uid": "should-be-skipped",
        }
        request.resolver_match = mock_resolver_match

        context = Context(
            {
                "request": request,
                "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                "simple_form": types.SimpleNamespace(form=SimpleForm()),
            }
        )
        html = t.render(context)

        assert "_url_param_id" in html or 'name="_url_param_id"' in html
        assert 'value="123"' in html
        assert "_url_param_slug" in html or 'name="_url_param_slug"' in html
        assert 'value="test-slug"' in html
        assert 'name="_url_param_uid"' not in html
