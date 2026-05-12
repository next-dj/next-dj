import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.template import Context, TemplateSyntaxError

from next.forms import (
    RegistryFormActionBackend,
    form_action_manager,
)
from next.templatetags.forms import _parse_form_tag_args
from tests.forms.actions import SimpleForm


PAGE_MODULE_FOR_FORM_TESTS = (
    Path(__file__).resolve().parent.parent / "site_pages" / "page.py"
).resolve()


class TestRenderFormFragment:
    """render_form_fragment: unknown action, template_fragment, fallback, context."""

    def test_unknown_action_returns_empty(self, mock_http_request) -> None:
        """Unknown action renders empty string."""
        request = mock_http_request(method="GET")
        html = form_action_manager.render_form_fragment(
            request, "unknown_action_xyz", None
        )
        assert html == ""

    def test_with_template_fragment(self, mock_http_request) -> None:
        """Render form using the page template for ``page_file_path``."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "test"})
        html = form_action_manager.render_form_fragment(
            request,
            "test_submit",
            form,
            template_fragment=None,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert "test" in html or "name" in html

    def test_with_form_only_no_template(self, mock_http_request) -> None:
        """Render form via registry template for ``page_file_path`` returns HTML."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "x"})
        html = form_action_manager.render_form_fragment(
            request,
            "test_submit",
            form,
            template_fragment=None,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert isinstance(html, str)
        assert html.strip() != ""

    def test_form_none_no_template_returns_string(self, mock_http_request) -> None:
        """Form None still returns a string when a page template exists."""
        request = mock_http_request(method="GET")
        html = form_action_manager.render_form_fragment(
            request,
            "test_submit",
            form=None,
            template_fragment=None,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert isinstance(html, str)

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
            "test_submit",
            form,
            template_fragment=None,
            page_file_path=page_file,
        )
        if output_mode == "path":
            assert str(template_djx) in html
        else:
            assert "name" in html


class TestFormTagParse:
    """_parse_form_tag_args: quoted and unquoted values."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                '@action="submit_contact" class="my-form"',
                {"@action": "submit_contact", "class": "my-form"},
            ),
            ("foo=bar", {"foo": "bar"}),
        ],
        ids=("quoted", "unquoted"),
    )
    def test_parses_key_value_pairs(self, raw: str, expected: dict[str, str]) -> None:
        """Parse tag string into key value pairs."""
        args = _parse_form_tag_args(raw)
        assert args == expected


class TestFormTagSyntax:
    """{% form %} tag: required @action, syntax errors."""

    def test_requires_at_least_one_arg(self, form_engine) -> None:
        """{% form %} without args raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError):
            form_engine.from_string("{% form %}x{% endform %}")

    def test_requires_action_name_raises(self, form_engine) -> None:
        """{% form %} without @action raises with 'requires' message."""
        with pytest.raises(TemplateSyntaxError, match="requires"):
            form_engine.from_string("{% form x=1 %}y{% endform %}")


class TestFormTagRender:
    """{% form %} tag: attributes, CSRF, unknown action, no request."""

    def test_renders_attributes(self, form_engine, csrf_request) -> None:
        """Form tag renders action, method, form content."""
        t = form_engine.from_string(
            '{% form @action="test_submit" %}{{ form.as_p }}{% endform %}'
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

    def test_renders_extra_html_attrs(self, form_engine, csrf_request) -> None:
        """Form tag passes through class, id and other HTML attrs."""
        t = form_engine.from_string(
            '{% form @action="test_submit" class="my-form" id="f1" %}x{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert 'class="my-form"' in html
        assert 'id="f1"' in html

    def test_resolves_action_from_context_variable(
        self, form_engine, csrf_request
    ) -> None:
        """Unquoted @action resolves against the template context at render time.

        This lets a composite component reuse one template for several action
        targets (for example admin's add and change paths) without forcing the
        caller to duplicate the entire form block per branch.
        """
        t = form_engine.from_string("{% form @action=action_key %}x{% endform %}")
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                    "action_key": "test_submit",
                }
            )
        )
        # When the action name resolves to a registered action, its UID URL
        # is rendered. The literal variable reference must not survive.
        assert "action_key" not in html
        assert 'action=""' not in html
        assert "/_next/form/" in html

    def test_accepts_single_quoted_action(self, form_engine, csrf_request) -> None:
        """Single-quoted @action parses as a literal alongside double-quoted form."""
        t = form_engine.from_string("{% form @action='test_submit' %}x{% endform %}")
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "/_next/form/" in html
        assert 'action=""' not in html

    def test_resolves_html_attr_from_context_variable(
        self, form_engine, csrf_request
    ) -> None:
        """Unquoted attribute values resolve against the context too."""
        t = form_engine.from_string(
            '{% form @action="test_submit" class=form_class %}x{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                    "form_class": "space-y-4",
                }
            )
        )
        assert 'class="space-y-4"' in html

    def test_includes_csrf_when_request_in_context(
        self, form_engine, csrf_request
    ) -> None:
        """Form includes csrfmiddlewaretoken when request in context."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')
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
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')
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

    def test_requires_current_page_module_path(self, form_engine, csrf_request) -> None:
        """Without current_page_module_path, {% form %} raises ImproperlyConfigured."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')
        with pytest.raises(ImproperlyConfigured, match="current_page_module_path"):
            t.render(Context({"request": csrf_request}))

    def test_unknown_action_renders_empty_action_url(
        self, form_engine, csrf_request
    ) -> None:
        """Unknown action still renders form with empty action URL."""
        t = form_engine.from_string(
            '{% form @action="nonexistent_action_xyz" %}z{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert 'action=""' in html or "action=''" in html
        assert "<form" in html

    def test_without_request_in_context_raises(self, form_engine) -> None:
        """Form without request in context raises ImproperlyConfigured."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')
        with pytest.raises(ImproperlyConfigured, match=r"request.*in template context"):
            t.render(Context({}))

    def test_form_variable_is_local(self, form_engine, csrf_request) -> None:
        """Form variable is only available inside {% form %} tag."""
        form_instance = SimpleForm(initial={"name": "test"})
        t = form_engine.from_string(
            'Outside: {{ form|default:"none" }} '
            '{% form @action="test_submit" %}Inside: {{ form.name.value|default:"none" }}{% endform %} '
            'Outside: {{ form|default:"none" }}'
        )
        context = Context(
            {
                "request": csrf_request,
                "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                "test_submit": types.SimpleNamespace(form=form_instance),
            }
        )
        html = t.render(context)
        # Form should be available inside tag (should show form value, not "none")
        assert "test" in html
        assert "Inside:" in html
        # Form should not be available outside tag (should show "none" twice)
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
                "test_submit": types.SimpleNamespace(form=form_instance),
            }
        )
        t = form_engine.from_string(
            '{% form @action="test_submit" %}{{ form.name.value }}{% endform %}'
        )
        html = t.render(context)
        assert "test_name" in html

    def test_form_includes_url_parameters_as_hidden_fields(
        self, form_engine, csrf_request
    ) -> None:
        """Form includes hidden fields for URL parameters from resolver_match."""
        t = form_engine.from_string('{% form @action="test_submit" %}x{% endform %}')

        # Create a request with resolver_match containing URL parameters
        request = HttpRequest()
        request.method = "GET"
        get_token(request)

        # Mock resolver_match with kwargs containing URL parameters
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
                "test_submit": types.SimpleNamespace(form=SimpleForm()),
            }
        )
        html = t.render(context)

        # Check that hidden fields for URL parameters are included (covers lines 156-159)
        assert "_url_param_id" in html or 'name="_url_param_id"' in html
        assert 'value="123"' in html
        assert "_url_param_slug" in html or 'name="_url_param_slug"' in html
        assert 'value="test-slug"' in html
        # uid should be skipped (line 158)
        assert 'name="_url_param_uid"' not in html
