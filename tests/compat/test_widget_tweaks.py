import pytest
from django import forms as django_forms
from django.conf import settings
from django.http import HttpRequest, HttpResponseRedirect
from django.template import engines
from django.test import override_settings

from next.forms import Form
from next.forms.manager import form_action_manager


pytest.importorskip("widget_tweaks")


class CompatTweaksForm(Form):
    """Form whose field is restyled by widget-tweaks filters."""

    name = django_forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Redirect once the submission validates."""
        return HttpResponseRedirect("/done/")


TWEAKS_SOURCE = (
    "{% load widget_tweaks %}"
    '{% form "compat_tweaks_form" %}'
    '{{ form.name|add_class:"form-control custom-input"|attr:"data-test:tweaked"|attr:"placeholder:Your name" }}'
    "{{ form.name.errors }}"
    "{% endform %}"
)


@pytest.fixture()
def tweaks_env():
    """Enable the widget_tweaks template library for one test."""
    with override_settings(
        INSTALLED_APPS=[*settings.INSTALLED_APPS, "widget_tweaks"],
    ):
        yield


class TestWidgetTweaks:
    """add_class and attr filters on the pushed form variable."""

    def test_filters_apply_to_unbound_field(self, tweaks_env, csrf_request):
        html = (
            engines["django"]
            .from_string(TWEAKS_SOURCE)
            .render({"request": csrf_request})
        )
        assert 'class="form-control custom-input"' in html
        assert 'data-test="tweaked"' in html
        assert 'placeholder="Your name"' in html

    def test_filters_survive_invalid_rerender(self, tweaks_env, csrf_request, tmp_path):
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        (tmp_path / "template.djx").write_text(TWEAKS_SOURCE)
        form = CompatTweaksForm(data={"name": ""})
        assert not form.is_valid()
        html = form_action_manager.default_backend.render_invalid_page(
            csrf_request, "compat_tweaks_form", form, page_file_path=page_file
        )
        assert 'class="form-control custom-input"' in html
        assert 'data-test="tweaked"' in html
        assert "errorlist" in html

    def test_invalid_post_sets_invalid_header(self, tweaks_env, next_client):
        resp = next_client.post_action("compat_tweaks_form", {"name": ""}, origin="/")
        assert resp.status_code == 200
        assert resp["X-Next-Form"] == "invalid"

    def test_valid_post_redirects(self, tweaks_env, next_client):
        resp = next_client.post_action(
            "compat_tweaks_form", {"name": "Ada"}, origin="/"
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/done/"
