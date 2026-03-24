"""Component context for the global header (next.components, not next.pages)."""

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import HttpRequest

from next.components import component


@component.context("user")
def bind_user(request: HttpRequest) -> AbstractBaseUser | AnonymousUser:
    """Expose the current user (anonymous included)."""
    return request.user
