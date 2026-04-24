from __future__ import annotations

import secrets

from django import forms
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from shortener.models import Link

from next.forms import Form, action
from next.pages import context


SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
SLUG_ATTEMPTS_PER_LENGTH = 10
SLUG_MAX_LENGTH = 12


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


def _random_slug(length: int) -> str:
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(length))


def _create_link_with_unique_slug(url: str, length: int = 6) -> Link:
    """Create a `Link` by trying random slugs until one passes the unique constraint."""
    while length <= SLUG_MAX_LENGTH:
        for _ in range(SLUG_ATTEMPTS_PER_LENGTH):
            candidate = _random_slug(length)
            try:
                with transaction.atomic():
                    return Link.objects.create(slug=candidate, url=url)
            except IntegrityError:
                continue
        length += 1
    msg = f"Could not allocate a unique slug within {SLUG_MAX_LENGTH} characters"
    raise RuntimeError(msg)


@context("recent_links")
def recent_links() -> list[Link]:
    return list(Link.objects.all()[:5])


@action("create_link", form_class=CreateLinkForm)
def create_link(form: CreateLinkForm) -> HttpResponseRedirect:
    _create_link_with_unique_slug(form.cleaned_data["url"])
    return HttpResponseRedirect("/")
