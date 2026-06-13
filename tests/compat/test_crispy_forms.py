import types
import warnings

import pytest
from django import forms as django_forms
from django.conf import settings
from django.http import HttpRequest, HttpResponseRedirect
from django.template import engines
from django.test import override_settings

from next.forms import Form
from next.forms.manager import form_action_manager


crispy_helper = pytest.importorskip("crispy_forms.helper")
pytest.importorskip("crispy_bootstrap5")


class CompatCrispyFilterForm(Form):
    """Zero-config form rendered through the |crispy filter."""

    name = django_forms.CharField(max_length=100)
    email = django_forms.EmailField()

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Redirect once the submission validates."""
        return HttpResponseRedirect("/done/")


class CompatCrispyHelperForm(Form):
    """Form whose helper cedes the form element and CSRF to {% form %}."""

    name = django_forms.CharField(max_length=100)

    @property
    def helper(self) -> crispy_helper.FormHelper:
        """Build a helper with crispy's own form tag and CSRF node disabled."""
        helper = crispy_helper.FormHelper()
        helper.form_tag = False
        helper.disable_csrf = True
        return helper


FILTER_SOURCE = (
    "{% load crispy_forms_tags %}"
    '{% form "compat_crispy_filter_form" %}'
    "{{ form|crispy }}"
    '<button type="submit">Send</button>'
    "{% endform %}"
)

HELPER_SOURCE = (
    "{% load crispy_forms_tags %}"
    '{% form "compat_crispy_helper_form" %}'
    "{% crispy form %}"
    "{% endform %}"
)


@pytest.fixture()
def crispy_env():
    """Enable the crispy apps and the bootstrap5 template pack for one test."""
    with override_settings(
        INSTALLED_APPS=[*settings.INSTALLED_APPS, "crispy_forms", "crispy_bootstrap5"],
        CRISPY_ALLOWED_TEMPLATE_PACKS="bootstrap5",
        CRISPY_TEMPLATE_PACK="bootstrap5",
    ):
        yield


def _render(source: str, context: dict[str, object]) -> str:
    return engines["django"].from_string(source).render(context)


class TestCrispyFilter:
    """The |crispy filter on the pushed form variable inside {% form %}."""

    def test_renders_bootstrap5_markup_single_form(self, crispy_env, csrf_request):
        csrf_request.path = "/"
        html = _render(FILTER_SOURCE, {"request": csrf_request})
        assert html.count("<form") == 1
        assert html.count("csrfmiddlewaretoken") == 1
        assert "_next_form_origin" in html
        assert 'id="div_id_name"' in html
        assert "form-control" in html

    def test_emits_data_next_action_uid(self, crispy_env, csrf_request):
        html = _render(FILTER_SOURCE, {"request": csrf_request})
        meta = form_action_manager.get_action_meta("compat_crispy_filter_form")
        assert meta is not None
        assert f'data-next-action="{meta["uid"]}"' in html

    def test_bound_errors_render_invalid_classes(self, crispy_env, csrf_request):
        form = CompatCrispyFilterForm(data={"name": "", "email": "nope"})
        assert not form.is_valid()
        html = _render(
            FILTER_SOURCE,
            {
                "request": csrf_request,
                "compat_crispy_filter_form": types.SimpleNamespace(form=form),
            },
        )
        assert "is-invalid" in html
        assert "invalid-feedback" in html

    def test_invalid_rerender_keeps_crispy_markup(
        self, crispy_env, csrf_request, tmp_path
    ):
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        (tmp_path / "template.djx").write_text(FILTER_SOURCE)
        form = CompatCrispyFilterForm(data={"name": "Ada", "email": "nope"})
        assert not form.is_valid()
        html = form_action_manager.default_backend.render_invalid_page(
            csrf_request,
            "compat_crispy_filter_form",
            form,
            page_file_path=page_file,
        )
        assert "is-invalid" in html
        assert 'value="Ada"' in html
        assert html.count("csrfmiddlewaretoken") == 1


class TestCrispyHelperTag:
    """{% crispy form %} with form_tag=False and disable_csrf=True."""

    def test_renders_single_form_and_single_csrf(self, crispy_env, csrf_request):
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            html = _render(HELPER_SOURCE, {"request": csrf_request})
        assert html.count("<form") == 1
        assert html.count("csrfmiddlewaretoken") == 1
        assert 'name="name"' in html


class TestCrispyDispatch:
    """HTTP round trip for a crispy-rendered next form."""

    def test_invalid_post_rerenders_with_header(self, crispy_env, next_client):
        resp = next_client.post_action(
            "compat_crispy_filter_form", {"name": "", "email": "nope"}, origin="/"
        )
        meta = form_action_manager.get_action_meta("compat_crispy_filter_form")
        assert resp.status_code == 200
        assert resp["X-Next-Form"] == "invalid"
        assert meta is not None
        assert resp["X-Next-Action"] == meta["uid"]

    def test_valid_post_redirects(self, crispy_env, next_client):
        resp = next_client.post_action(
            "compat_crispy_filter_form",
            {"name": "Ada", "email": "ada@example.com"},
            origin="/",
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/done/"
