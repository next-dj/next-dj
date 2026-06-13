import pytest
from django import forms as django_forms
from django.conf import settings
from django.http import HttpRequest, HttpResponseRedirect
from django.template import Context
from django.test import override_settings

from next.forms import Form
from next.forms.manager import form_action_manager


pytest.importorskip("django_htmx")


class CompatHtmxForm(Form):
    """Form whose on_valid branches on the django-htmx request annotation."""

    name = django_forms.CharField(max_length=100)
    email = django_forms.EmailField()

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Redirect to a marker URL depending on request.htmx."""
        if request.htmx:
            return HttpResponseRedirect("/htmx-submit/")
        return HttpResponseRedirect("/plain-submit/")


HX_SOURCE = (
    '{% form "compat_htmx_form" hx-boost="true" hx-select="#contact" hx-target="#contact" hx-swap="outerHTML" %}'
    '<div id="contact">{{ form.as_div }}</div>'
    "{% endform %}"
)


@pytest.fixture()
def htmx_env():
    """Append HtmxMiddleware to the middleware chain for one test."""
    with override_settings(
        MIDDLEWARE=[*settings.MIDDLEWARE, "django_htmx.middleware.HtmxMiddleware"],
    ):
        yield


class TestHtmxAttributePassthrough:
    """hx-* attributes flow through the {% form %} attribute parser."""

    def test_hx_attributes_reach_the_opening_tag(self, form_engine, csrf_request):
        html = form_engine.from_string(HX_SOURCE).render(
            Context({"request": csrf_request})
        )
        meta = form_action_manager.get_action_meta("compat_htmx_form")
        assert meta is not None
        assert 'hx-boost="true"' in html
        assert 'hx-select="#contact"' in html
        assert 'hx-target="#contact"' in html
        assert 'hx-swap="outerHTML"' in html
        assert f'action="/_next/form/{meta["uid"]}/"' in html
        assert f'data-next-action="{meta["uid"]}"' in html

    def test_explicit_hx_post_keeps_framework_action(self, form_engine, csrf_request):
        source = (
            '{% form "compat_htmx_form" hx-post="/manual-endpoint/" %}x{% endform %}'
        )
        html = form_engine.from_string(source).render(
            Context({"request": csrf_request})
        )
        assert 'hx-post="/manual-endpoint/"' in html
        assert 'action="/_next/form/' in html

    def test_invalid_rerender_keeps_hx_attributes(self, csrf_request, tmp_path):
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        (tmp_path / "template.djx").write_text(HX_SOURCE)
        form = CompatHtmxForm(data={"name": "Ada", "email": "nope"})
        assert not form.is_valid()
        html = form_action_manager.default_backend.render_invalid_page(
            csrf_request, "compat_htmx_form", form, page_file_path=page_file
        )
        assert 'hx-boost="true"' in html
        assert 'hx-select="#contact"' in html
        assert 'value="Ada"' in html


class TestHtmxMiddlewareDispatch:
    """HtmxMiddleware coexists with dispatch and feeds request.htmx."""

    def test_request_htmx_reaches_on_valid(self, htmx_env, next_client):
        resp = next_client.post_action(
            "compat_htmx_form",
            {"name": "Ada", "email": "ada@example.com"},
            origin="/",
            headers={"hx-request": "true", "hx-boosted": "true"},
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/htmx-submit/"

    def test_plain_post_sees_falsy_htmx(self, htmx_env, next_client):
        resp = next_client.post_action(
            "compat_htmx_form",
            {"name": "Ada", "email": "ada@example.com"},
            origin="/",
        )
        assert resp.status_code == 302
        assert resp["Location"] == "/plain-submit/"

    def test_invalid_htmx_post_returns_full_page(self, htmx_env, next_client):
        resp = next_client.post_action(
            "compat_htmx_form",
            {"name": "Ada", "email": "nope"},
            origin="/",
            headers={"hx-request": "true"},
        )
        assert resp.status_code == 200
        assert resp["X-Next-Form"] == "invalid"
        content = resp.content.decode()
        assert "test page" in content
        assert 'value="Ada"' in content
