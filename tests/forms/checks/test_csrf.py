from pathlib import Path
from typing import Any, ClassVar

import pytest
from django import forms as django_forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template import Template
from django.test import override_settings

from next.checks import reset_check_caches
from next.forms import Form, FormWizard, action
from next.forms.checks import check_shared_page_forms
from next.forms.checks.csrf import _posts_bare
from next.forms.manager import form_action_manager
from next.forms.nodes import FormNode
from tests.support import check_ids, routed, write_page


PAGE = Path(__file__)

RUNTIME = "<html><body>{% template %}{% collect_scripts %}</body></html>"
FORM_PAGE = """
from next.forms import action

template = '{% form "ping" %}{% endform %}'
cache = 60


@action("ping")
def ping():
    return None
"""
RUNTIME_FORM_PAGE = FORM_PAGE.replace(
    '@action("ping")', '@action("ping", requires_runtime=True)'
)
RUNTIME_FORM_CLASS_PAGE = """
from django import forms as django_forms

from next.forms import Form

template = '{% form "signup_form" %}{% endform %}'
cache = 60


class SignupForm(Form):
    email = django_forms.EmailField()

    class Meta:
        requires_runtime = True
"""


class RuntimeOnlySignupForm(Form):
    """A form the page posts only through the client runtime."""

    email = django_forms.EmailField()

    class Meta:
        """Posts only through the runtime."""

        requires_runtime = True


class RuntimeOnlyStep(Form):
    """The one step of the runtime-only wizard."""

    name = django_forms.CharField()

    class Meta:
        """A step, never a standalone action."""

        abstract = True


class RuntimeOnlyWizard(FormWizard):
    """A wizard whose steps post only through the client runtime."""

    class Meta:
        """One step, posting only through the runtime."""

        steps: ClassVar = [("name", RuntimeOnlyStep)]
        requires_runtime = True

    def done(
        self, request: HttpRequest, cleaned_data: dict[str, Any]
    ) -> HttpResponseRedirect:
        return HttpResponseRedirect("/done/")


@action("runtime_only_vote", requires_runtime=True)
def runtime_only_vote() -> HttpResponse:
    return HttpResponse("ok")


@action("bare_vote")
def bare_vote() -> HttpResponse:
    return HttpResponse("ok")


def _posts_bare_anywhere(body: str) -> bool:
    nodelist = Template("{% load forms %}" + body).nodelist
    return any(_posts_bare(node, PAGE) for node in nodelist.get_nodes_by_type(FormNode))


class TestRequiresRuntime:
    """An action declares that its forms post only through the client runtime."""

    @pytest.mark.parametrize(
        "name",
        ["runtime_only_signup_form", "runtime_only_wizard", "runtime_only_vote"],
        ids=["form-meta", "wizard-meta", "action-kwarg"],
    )
    def test_the_declaration_lands_in_the_registry(self, name: str) -> None:
        meta = form_action_manager.get_action_meta(name)
        assert meta is not None
        assert meta.get("requires_runtime") is True

    def test_an_undeclared_action_carries_no_key(self) -> None:
        meta = form_action_manager.get_action_meta("bare_vote")
        assert meta is not None
        assert "requires_runtime" not in meta

    @pytest.mark.parametrize(
        "body",
        [
            '{% form "runtime_only_signup_form" %}{% endform %}',
            (
                '{% form "runtime_only_vote" %}{% endform %}'
                '{% form "runtime_only_wizard" %}{% endform %}'
            ),
            "<p>no form</p>",
        ],
        ids=["form", "action-and-wizard", "none"],
    )
    def test_runtime_only_forms_need_no_fallback(self, body: str) -> None:
        assert _posts_bare_anywhere(body) is False

    @pytest.mark.parametrize(
        "body",
        [
            '{% form "bare_vote" %}{% endform %}',
            (
                '{% form "runtime_only_vote" %}{% endform %}'
                '{% form "bare_vote" %}{% endform %}'
            ),
            "{% form action_name %}{% endform %}",
            '{% form "runtime_only_vote"|lower %}{% endform %}',
            '{% form "never_registered" %}{% endform %}',
        ],
        ids=["undeclared", "one-of-two", "variable", "filtered", "unknown"],
    )
    def test_a_form_that_may_post_bare_needs_the_fallback(self, body: str) -> None:
        assert _posts_bare_anywhere(body) is True


def _tree(tmp_path: Path, source: str, layout: str = "{% template %}") -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(layout)
    write_page(root, "", source)
    return root


@pytest.fixture()
def _fresh_run():
    reset_check_caches()
    yield
    reset_check_caches()


@pytest.mark.usefixtures("_fresh_run")
class TestSharedPageForms:
    """`next.W121` and `next.W124` warn about the forms of a page a CDN may hold."""

    def test_a_private_page_is_silent(self, tmp_path) -> None:
        source = FORM_PAGE.replace("cache = 60\n", "")
        with routed(_tree(tmp_path, source, RUNTIME), CSRF_DELIVERY="eager"):
            assert check_shared_page_forms() == []

    def test_a_shared_form_without_js_is_w124(self, tmp_path) -> None:
        with routed(_tree(tmp_path, FORM_PAGE)):
            assert check_ids(check_shared_page_forms()) == ["next.W124"]

    @pytest.mark.parametrize(
        "source",
        [RUNTIME_FORM_PAGE, RUNTIME_FORM_CLASS_PAGE],
        ids=["action", "form-class"],
    )
    def test_a_form_posting_only_through_the_runtime_is_silent(
        self, tmp_path, source
    ) -> None:
        with override_settings(BASE_DIR=tmp_path), routed(_tree(tmp_path, source)):
            assert check_shared_page_forms() == []

    def test_one_form_that_may_post_bare_keeps_w124(self, tmp_path) -> None:
        source = (
            RUNTIME_FORM_PAGE.replace(
                "{% endform %}'", '{% endform %}{% form "pong" %}{% endform %}\''
            )
            + '\n\n@action("pong")\ndef pong():\n    return None\n'
        )
        with routed(_tree(tmp_path, source)):
            [warning] = check_shared_page_forms()
        assert warning.id == "next.W124"
        assert "declare requires_runtime on the action" in warning.msg

    @pytest.mark.parametrize(
        ("source", "layout"),
        [(FORM_PAGE, "{% template %}"), ("template = 'x'\ncache = 60\n", RUNTIME)],
        ids=["form", "runtime"],
    )
    def test_eager_delivery_on_a_shared_page_is_w121(
        self, tmp_path, source, layout
    ) -> None:
        with routed(_tree(tmp_path, source, layout), CSRF_DELIVERY="eager"):
            assert check_ids(check_shared_page_forms()) == ["next.W121"]
