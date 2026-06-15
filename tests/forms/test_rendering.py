import types
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from django import forms as django_forms
from django.core.exceptions import ImproperlyConfigured
from django.forms import formset_factory
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict
from django.middleware.csrf import get_token
from django.template import Context, TemplateSyntaxError

from next.forms import (
    ActionRegistration,
    Form,
    FormActionBackend,
    FormActionNotFound,
    RegistryFormActionBackend,
)
from next.forms.manager import form_action_manager
from next.forms.rendering import _ErrorRenderParams, render_form_page_with_errors
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


class UploadEnctypeForm(Form):
    """File-upload form used by the auto-enctype tests."""

    doc = django_forms.FileField()


class _BulkRowForm(django_forms.Form):
    """Single row of the formset rendered through the form tag."""

    title = django_forms.CharField(max_length=20)


_BulkRowFormset = formset_factory(_BulkRowForm, extra=1)


class TestRenderInvalidPage:
    """render_invalid_page covers unknown actions, page templates, and fallbacks."""

    def test_unknown_action_returns_empty(self, mock_http_request) -> None:
        """Unknown action renders empty string."""
        request = mock_http_request(method="GET")
        html = form_action_manager.default_backend.render_invalid_page(
            request, "unknown_action_xyz", None
        )
        assert html == ""

    def test_renders_with_page_template(self, mock_http_request) -> None:
        """Render form using the page template for ``page_file_path``."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "test"})
        html = form_action_manager.default_backend.render_invalid_page(
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
        html = form_action_manager.default_backend.render_invalid_page(
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
        html = form_action_manager.default_backend.render_invalid_page(
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
            _ErrorRenderParams(
                action_name="unknown_action_xyz", form=form, url_kwargs={}
            ),
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
            _ErrorRenderParams(
                action_name="unknown_action_xyz", form=None, url_kwargs={}
            ),
            PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert html == ""

    def test_wizard_rerender_without_origin_uses_empty_base_path(
        self, mock_http_request
    ) -> None:
        """A wizard re-render with no resolvable origin still renders."""
        request = mock_http_request(method="POST", POST=QueryDict())
        form = RenderWizardStep(data={"name": ""})
        assert not form.is_valid()
        html = form_action_manager.default_backend.render_invalid_page(
            request,
            "render_wizard",
            form,
            page_file_path=PAGE_MODULE_FOR_FORM_TESTS,
        )
        assert isinstance(html, str)
        assert html.strip() != ""

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
        """Render the page reading the sibling template.djx of ``page_file_path``."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "a"})
        backend = form_action_manager.default_backend
        assert isinstance(backend, RegistryFormActionBackend)

        page_file = tmp_path / "page.py"
        page_file.write_text("")
        template_djx = tmp_path / "template.djx"
        template_djx.write_text(template_body)

        html = backend.render_invalid_page(
            request,
            "simple_form",
            form,
            page_file_path=page_file,
        )
        if output_mode == "path":
            assert str(template_djx) in html
        else:
            assert "name" in html

    def test_rerender_picks_up_template_and_layout_edits(
        self, mock_http_request, tmp_path
    ) -> None:
        """Editing template.djx or an ancestor layout.djx invalidates the cache."""
        request = mock_http_request(method="GET")
        form = SimpleForm(initial={"name": "a"})
        backend = form_action_manager.default_backend

        layout = tmp_path / "layout.djx"
        layout.write_text("<html>{% block template %}{% endblock template %}</html>")
        leaf = tmp_path / "leaf"
        leaf.mkdir()
        page_file = leaf / "page.py"
        page_file.write_text("")
        template_djx = leaf / "template.djx"
        template_djx.write_text("<p>v1</p>")

        first = backend.render_invalid_page(
            request, "simple_form", form, page_file_path=page_file
        )
        assert "<p>v1</p>" in first
        assert "<html>" in first

        template_djx.write_text("<p>v2</p>")
        second = backend.render_invalid_page(
            request, "simple_form", form, page_file_path=page_file
        )
        assert "<p>v2</p>" in second

        layout.write_text("<main>{% block template %}{% endblock template %}</main>")
        third = backend.render_invalid_page(
            request, "simple_form", form, page_file_path=page_file
        )
        assert "<main>" in third
        assert "<p>v2</p>" in third


class TestFormTagSyntax:
    """The {% form %} tag requires an action name and rejects bad syntax."""

    def test_requires_at_least_one_arg(self, form_engine) -> None:
        """{% form %} without args raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError):
            form_engine.from_string("{% form %}x{% endform %}")

    def test_positional_second_arg_raises(self, form_engine) -> None:
        """{% form %} with a second positional argument raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError, match=r'key="value"'):
            form_engine.from_string('{% form "foo" "bar" %}x{% endform %}')

    @pytest.mark.parametrize(
        "attr",
        ["action", "method", "data-next-action", "data-next-target"],
    )
    def test_reserved_attribute_raises(self, form_engine, attr: str) -> None:
        """{% form %} rejects exact reserved names and the data-next- prefix."""
        with pytest.raises(
            TemplateSyntaxError,
            match=f"reserves the '{attr}' attribute for the framework",
        ):
            form_engine.from_string(
                f'{{% form "simple_form" {attr}="/x/" %}}x{{% endform %}}'
            )


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

    def test_renders_enctype_attribute(self, form_engine, csrf_request) -> None:
        """An enctype attribute lands on the opening form element."""
        t = form_engine.from_string(
            '{% form "simple_form" enctype="multipart/form-data" %}x{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        meta = form_action_manager.get_action_meta(
            "simple_form", page_path=str(PAGE_MODULE_FOR_FORM_TESTS)
        )
        assert meta is not None
        assert (
            f'method="post" data-next-action="{meta["uid"]}"'
            ' enctype="multipart/form-data">'
        ) in html

    def test_renders_multiple_attributes_in_order(
        self, form_engine, csrf_request
    ) -> None:
        """Attributes render in declaration order, author literals verbatim."""
        t = form_engine.from_string(
            '{% form "simple_form" enctype="multipart/form-data"'
            ' class="stack wide" data-info="a&b" %}x{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert (
            'enctype="multipart/form-data" class="stack wide" data-info="a&b">' in html
        )

    def test_attribute_value_from_context_is_escaped(
        self, form_engine, csrf_request
    ) -> None:
        """A context-sourced attribute value is conditionally escaped."""
        t = form_engine.from_string(
            '{% form "simple_form" data-info=info %}x{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                    "info": 'a&b "quoted"',
                }
            )
        )
        assert 'data-info="a&amp;b &quot;quoted&quot;">' in html

    def test_attribute_value_resolves_from_context(
        self, form_engine, csrf_request
    ) -> None:
        """An unquoted attribute value resolves as a context variable."""
        t = form_engine.from_string(
            '{% form "simple_form" class=css_class %}x{% endform %}'
        )
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                    "css_class": "from-ctx",
                }
            )
        )
        assert 'class="from-ctx">' in html

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

    def test_emits_no_page_path_hidden_field(self, form_engine, csrf_request) -> None:
        """The form never leaks the page source path into the markup."""
        t = form_engine.from_string('{% form "simple_form" %}x{% endform %}')
        html = t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert "_next_form_page" not in html
        assert "page.py" not in html
        assert str(PAGE_MODULE_FOR_FORM_TESTS) not in html

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

    @staticmethod
    def _rerender_request(csrf_request, post: dict) -> object:
        csrf_request.method = "POST"
        csrf_request.path = "/_next/form/abc123/"
        csrf_request.POST = post
        csrf_request.resolver_match = MagicMock()
        csrf_request.resolver_match.kwargs = {"uid": "abc123"}
        return csrf_request

    def test_error_rerender_keeps_posted_origin(
        self, form_engine, csrf_request
    ) -> None:
        """On the action-POST re-render the posted origin wins over request.path."""
        request = self._rerender_request(
            csrf_request, {"_next_form_origin": "/board/4/settings/"}
        )
        t = form_engine.from_string('{% form "simple_form" %}x{% endform %}')
        html = t.render(
            Context(
                {
                    "request": request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )
        assert 'name="_next_form_origin" value="/board/4/settings/"' in html
        assert 'value="/_next/form/abc123/"' not in html

    def test_unknown_action_raises_form_action_not_found(
        self, form_engine, csrf_request
    ) -> None:
        """Unknown action lets FormActionNotFound propagate at render time."""
        t = form_engine.from_string('{% form "nonexistent_action_xyz" %}z{% endform %}')
        with pytest.raises(FormActionNotFound, match="Unknown form action"):
            t.render(
                Context(
                    {
                        "request": csrf_request,
                        "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                    }
                )
            )

    def test_unquoted_action_name_raises_with_quoting_hint(
        self, form_engine, csrf_request
    ) -> None:
        """An unquoted action token names itself and suggests the quoted literal."""
        t = form_engine.from_string("{% form simple_form %}x{% endform %}")
        with pytest.raises(
            FormActionNotFound, match=r'write \{% form "simple_form" %\}'
        ):
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

    def test_form_emits_no_url_parameter_hidden_fields(
        self, form_engine, csrf_request
    ) -> None:
        """URL kwargs never become hidden fields, the origin carries them."""
        t = form_engine.from_string('{% form "simple_form" %}x{% endform %}')

        request = HttpRequest()
        request.method = "GET"
        request.path = "/items/123/"
        get_token(request)

        mock_resolver_match = MagicMock()
        mock_resolver_match.kwargs = {"id": 123}
        request.resolver_match = mock_resolver_match

        context = Context(
            {
                "request": request,
                "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                "simple_form": types.SimpleNamespace(form=SimpleForm()),
            }
        )
        html = t.render(context)

        assert "_url_param_" not in html
        assert 'name="_next_form_origin" value="/items/123/"' in html


_FORMSET_TAG_TEMPLATE = (
    '{% form "bulk_rows" %}'
    "{{ form.management_form }}"
    "{% for row in form %}<div>{{ row.title }}</div>{% endfor %}"
    "{% endform %}"
)


class TestFormTagFormsetRender:
    """The {% form %} tag renders formset actions through both documented paths."""

    @staticmethod
    def _render(form_engine, csrf_request, extra_context: dict | None = None) -> str:
        t = form_engine.from_string(_FORMSET_TAG_TEMPLATE)
        return t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                    **(extra_context or {}),
                }
            )
        )

    def test_factory_built_namespace_renders_formset(
        self, form_engine, csrf_request
    ) -> None:
        """A factory returning (FormSetClass, init_kwargs) renders rows and rails."""

        def build_bulk_rows():
            return _BulkRowFormset, {"initial": [{"title": "Draft"}]}

        form_action_manager.default_backend.register_action(
            ActionRegistration(
                name="bulk_rows",
                file_path=str(PAGE_MODULE_FOR_FORM_TESTS),
                scope="page",
                handler=lambda **_kwargs: None,
                form_class=build_bulk_rows,
            )
        )
        html = self._render(form_engine, csrf_request)
        assert 'name="form-TOTAL_FORMS"' in html
        assert 'value="Draft"' in html
        assert html.count('name="form-') >= 4
        assert "</form>" in html

    def test_context_namespace_renders_formset(self, form_engine, csrf_request) -> None:
        """A SimpleNamespace(form=formset) context entry renders rows and rails."""
        form_action_manager.default_backend.register_action(
            ActionRegistration(
                name="bulk_rows",
                file_path=str(PAGE_MODULE_FOR_FORM_TESTS),
                scope="page",
                handler=lambda: None,
            )
        )
        formset = _BulkRowFormset(initial=[{"title": "Draft"}])
        html = self._render(
            form_engine,
            csrf_request,
            {"bulk_rows": types.SimpleNamespace(form=formset)},
        )
        assert 'name="form-TOTAL_FORMS"' in html
        assert 'value="Draft"' in html
        assert "</form>" in html

    def test_bound_invalid_formset_re_renders_through_tag(
        self, form_engine, csrf_request
    ) -> None:
        """A bound formset with errors still renders, mirroring the error re-render."""
        form_action_manager.default_backend.register_action(
            ActionRegistration(
                name="bulk_rows",
                file_path=str(PAGE_MODULE_FOR_FORM_TESTS),
                scope="page",
                handler=lambda: None,
            )
        )
        formset = _BulkRowFormset(
            data={
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "1",
                "form-0-title": "",
            }
        )
        assert not formset.is_valid()
        html = self._render(
            form_engine,
            csrf_request,
            {"bulk_rows": types.SimpleNamespace(form=formset)},
        )
        assert 'name="form-TOTAL_FORMS"' in html
        assert "</form>" in html


class TestActionUrlTag:
    """{% action_url %} resolves page and shared scopes and rejects unknown names."""

    @staticmethod
    def _register_page_action(name: str, page_path: str) -> None:
        form_action_manager.default_backend.register_action(
            ActionRegistration(
                name=name,
                file_path=page_path,
                scope="page",
                handler=lambda: None,
            )
        )

    def test_resolves_page_scoped_action(self, form_engine, tmp_path) -> None:
        """The tag resolves a page-scoped action through the context page path."""
        page_path = str(tmp_path / "page.py")
        self._register_page_action("tag_page_action", page_path)
        out = form_engine.from_string('{% action_url "tag_page_action" %}').render(
            Context({"current_page_module_path": page_path})
        )
        assert out == form_action_manager.get_action_url(
            "tag_page_action", page_path=page_path
        )
        assert "/_next/form/" in out

    def test_page_scoped_action_invisible_from_other_page(
        self, form_engine, tmp_path
    ) -> None:
        """The tag honours the same scope filter as {% form %}."""
        page_a = str(tmp_path / "a" / "page.py")
        page_b = str(tmp_path / "b" / "page.py")
        self._register_page_action("tag_scoped_action", page_a)
        t = form_engine.from_string('{% action_url "tag_scoped_action" %}')
        with pytest.raises(FormActionNotFound, match="Unknown form action"):
            t.render(Context({"current_page_module_path": page_b}))

    def test_resolves_shared_action_without_page_path(self, form_engine) -> None:
        """A shared action resolves when the context carries no page path."""
        out = form_engine.from_string('{% action_url "test_no_form" %}').render(
            Context({})
        )
        assert out == form_action_manager.get_action_url("test_no_form")

    def test_as_variable(self, form_engine) -> None:
        """The as-form stores the URL in a context variable."""
        t = form_engine.from_string(
            '{% action_url "test_no_form" as target %}[{{ target }}]'
        )
        out = t.render(Context({}))
        expected = form_action_manager.get_action_url("test_no_form")
        assert out == f"[{expected}]"

    def test_unknown_action_raises_form_action_not_found(self, form_engine) -> None:
        """An unknown name lets FormActionNotFound propagate."""
        t = form_engine.from_string('{% action_url "nonexistent_action_xyz" %}')
        with pytest.raises(FormActionNotFound, match="Unknown form action"):
            t.render(Context({}))

    def test_unquoted_action_name_raises_with_quoting_hint(self, form_engine) -> None:
        """An empty resolved name points at the unresolved-variable cause."""
        t = form_engine.from_string("{% action_url save_note %}")
        with pytest.raises(FormActionNotFound, match="empty action name"):
            t.render(Context({}))


class TestFormTagMarkupIdentity:
    """The {% form %} tag emits data-next-action and the automatic enctype."""

    @staticmethod
    def _render(form_engine, csrf_request, source: str) -> str:
        t = form_engine.from_string(source)
        return t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )

    def test_emits_data_next_action_uid(self, form_engine, csrf_request) -> None:
        """The opening tag carries the registry uid as data-next-action."""
        html = self._render(
            form_engine, csrf_request, '{% form "simple_form" %}x{% endform %}'
        )
        meta = form_action_manager.get_action_meta(
            "simple_form", page_path=str(PAGE_MODULE_FOR_FORM_TESTS)
        )
        assert meta is not None
        uid = meta["uid"]
        assert (
            f'<form action="/_next/form/{uid}/" method="post" data-next-action="{uid}">'
        ) in html

    def test_handler_only_action_emits_data_next_action(
        self, form_engine, csrf_request
    ) -> None:
        """A handler-only action still carries the uid marker and no enctype."""
        html = self._render(
            form_engine, csrf_request, '{% form "test_no_form" %}x{% endform %}'
        )
        meta = form_action_manager.get_action_meta("test_no_form")
        assert meta is not None
        assert f'data-next-action="{meta["uid"]}">' in html
        assert "enctype" not in html

    def test_omits_data_next_action_without_meta(
        self, form_engine, csrf_request, monkeypatch
    ) -> None:
        """A backend exposing no meta renders the tag without the marker."""

        class NoMetaBackend(FormActionBackend):
            def register_action(self, *args: object, **kwargs: object) -> None:
                pass

            def get_action_url(self, action_name: str, **kwargs: object) -> str:
                return "/custom/submit/"

            def generate_urls(self) -> list:
                return []

            def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
                return HttpResponse()

        monkeypatch.setattr(form_action_manager, "_backends", [NoMetaBackend()])
        html = self._render(
            form_engine, csrf_request, '{% form "custom_action" %}x{% endform %}'
        )
        assert '<form action="/custom/submit/" method="post">' in html
        assert "data-next-action" not in html

    def test_auto_enctype_for_multipart_form(self, form_engine, csrf_request) -> None:
        """A multipart form gains enctype="multipart/form-data" automatically."""
        html = self._render(
            form_engine,
            csrf_request,
            '{% form "upload_enctype_form" %}x{% endform %}',
        )
        assert 'enctype="multipart/form-data">' in html

    def test_explicit_enctype_wins_over_auto(self, form_engine, csrf_request) -> None:
        """An author-passed enctype suppresses the multipart auto-attribute."""
        html = self._render(
            form_engine,
            csrf_request,
            '{% form "upload_enctype_form" enctype="text/plain" %}x{% endform %}',
        )
        assert 'enctype="text/plain">' in html
        assert "multipart/form-data" not in html


class TestFormTagPartialParams:
    """The validate, trigger, debounce, and zone params compile to data-next-*.

    The server authors the client attribute names so the markup never
    carries a raw selector or swap mode. Without the params the opening
    tag stays free of every partial attribute.
    """

    @staticmethod
    def _render(form_engine, csrf_request, source: str) -> str:
        t = form_engine.from_string(source)
        return t.render(
            Context(
                {
                    "request": csrf_request,
                    "current_page_module_path": str(PAGE_MODULE_FOR_FORM_TESTS),
                }
            )
        )

    def test_validate_blur_renders_the_validate_attribute(
        self, form_engine, csrf_request
    ) -> None:
        """A validate="blur" param renders data-next-validate="blur"."""
        html = self._render(
            form_engine,
            csrf_request,
            '{% form "simple_form" validate="blur" %}x{% endform %}',
        )
        assert 'data-next-validate="blur"' in html

    def test_every_partial_param_renders_its_attribute(
        self, form_engine, csrf_request
    ) -> None:
        """The four partial params render their data-next-* attributes together."""
        html = self._render(
            form_engine,
            csrf_request,
            '{% form "simple_form" validate="blur" trigger="change"'
            ' debounce="300" zone="rename-board" %}x{% endform %}',
        )
        assert 'data-next-validate="blur"' in html
        assert 'data-next-trigger="change"' in html
        assert 'data-next-debounce="300"' in html
        assert 'data-next-target="rename-board"' in html

    def test_no_partial_params_emit_no_partial_attributes(
        self, form_engine, csrf_request
    ) -> None:
        """A plain form tag carries none of the partial attributes."""
        html = self._render(
            form_engine, csrf_request, '{% form "simple_form" %}x{% endform %}'
        )
        assert "data-next-validate" not in html
        assert "data-next-trigger" not in html
        assert "data-next-debounce" not in html
        assert "data-next-target" not in html

    def test_partial_params_precede_author_attributes(
        self, form_engine, csrf_request
    ) -> None:
        """The partial attributes render before plain author attributes."""
        html = self._render(
            form_engine,
            csrf_request,
            '{% form "simple_form" validate="blur" class="stack" %}x{% endform %}',
        )
        assert html.index('data-next-validate="blur"') < html.index('class="stack"')
