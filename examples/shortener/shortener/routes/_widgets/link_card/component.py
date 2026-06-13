from django.urls import reverse
from shortener.models import Link

from next.components import component


@component.context("short_url")
def short_url(link: Link) -> str:
    return reverse("slug_redirect", kwargs={"slug": link.slug})
