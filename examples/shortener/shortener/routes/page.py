from __future__ import annotations

import secrets

from django import forms
from django.http import HttpResponseRedirect
from shortener.models import Link

from next.forms import Form, action
from next.pages import context


SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


class CreateLinkForm(Form):
    url = forms.URLField(
        max_length=2000,
        assume_scheme="https",
        widget=forms.URLInput(
            attrs={
                "class": (
                    "w-full rounded-lg border border-slate-300 px-3 py-2 "
                    "focus:border-slate-500 focus:outline-none"
                ),
                "placeholder": "https://example.com/very/long/path",
            }
        ),
    )


def _generate_slug(length: int = 6) -> str:
    while True:
        candidate = "".join(secrets.choice(SLUG_ALPHABET) for _ in range(length))
        if not Link.objects.filter(slug=candidate).exists():
            return candidate


@context("recent_links")
def _recent_links() -> list[Link]:
    return list(Link.objects.all()[:5])


@action("create_link", form_class=CreateLinkForm)
def _create_link(form: CreateLinkForm) -> HttpResponseRedirect:
    Link.objects.create(slug=_generate_slug(), url=form.cleaned_data["url"])
    return HttpResponseRedirect("/")
