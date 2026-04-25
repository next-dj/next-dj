from django.http import HttpRequest
from django.urls import reverse

from next.components import component


@component.context("href")
def _href(url_name: str) -> str:
    return reverse(url_name)


@component.context("is_active")
def _is_active(url_name: str, request: HttpRequest) -> bool:
    return request.resolver_match.view_name == url_name
