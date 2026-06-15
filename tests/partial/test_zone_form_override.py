from pathlib import Path

from django import forms as django_forms
from django.test import RequestFactory

from next.forms import Form
from next.partial import render_zone


FORMZONE_PAGE = (
    Path(__file__).resolve().parent.parent / "site_pages" / "formzone" / "page.py"
)


class EditorForm(Form):
    """Single-field form the zone body renders through an override."""

    name = django_forms.CharField(max_length=50)


def _request():
    """Return a plain GET request for the form zone page URL."""
    return RequestFactory().get("/formzone/")


class TestBoundFormOverride:
    """`overrides` puts a bound form into the zone context the body reads."""

    def test_bound_value_reaches_the_zone_body(self) -> None:
        form = EditorForm(data={"name": "Ada"})
        assert form.is_valid()
        result = render_zone(
            FORMZONE_PAGE,
            ("editor",),
            _request(),
            overrides={"form": form},
        )
        assert 'value="Ada"' in result.html["editor"]

    def test_bound_errors_reach_the_zone_body(self) -> None:
        form = EditorForm(data={"name": ""})
        assert not form.is_valid()
        result = render_zone(
            FORMZONE_PAGE,
            ("editor",),
            _request(),
            overrides={"form": form},
        )
        assert 'data-next-zone="editor"' in result.html["editor"]
        assert "required" in result.html["editor"].lower()
