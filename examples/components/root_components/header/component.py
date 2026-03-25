from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import HttpRequest

from next.components import context


@context("user")
def bind_user(request: HttpRequest) -> AbstractBaseUser | AnonymousUser:
    """Expose the current user (anonymous included)."""
    return request.user
