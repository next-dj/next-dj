from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import HttpRequest

from next.deps import resolver


@resolver.dependency("scope")
def scope(request: HttpRequest) -> AbstractBaseUser | AnonymousUser:
    """Return the visitor, the one value `AccessRequest.objects.visible_to` reads."""
    return request.user
