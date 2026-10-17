from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import HttpRequest

from next.deps import resolver


@resolver.dependency("scope")
def scope(request: HttpRequest) -> AbstractBaseUser | AnonymousUser:
    """Return the visitor, the scope `Book.objects.visible_to` narrows by."""
    return request.user
