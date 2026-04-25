from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import reverse

from next.components import component


if TYPE_CHECKING:
    from shortener.models import Link


@component.context("short_url")
def _short_url(link: Link) -> str:
    return reverse("slug_redirect", kwargs={"slug": link.slug})
