from access import services
from access.models import AccessRequest, AuditEntry
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from next import Depends, context


@context("recent_requests")
def recent_requests(
    user: AbstractBaseUser | AnonymousUser = Depends("scope"),
) -> list[AccessRequest]:
    """Return the five newest requests the visitor may see."""
    requests = AccessRequest.objects.visible_to(user)
    return list(requests.order_by("-created_at", "-pk")[:5])


@context("recent_audit")
def recent_audit() -> list[AuditEntry]:
    return services.recent_audit(5)
