from __future__ import annotations

from typing import TYPE_CHECKING

from next.deps import DDependencyBase, RegisteredParameterProvider
from notes.access import get_active_tenant


if TYPE_CHECKING:
    import inspect

    from next.deps.context import ResolutionContext
    from notes.models import Tenant


class DTenant(DDependencyBase["Tenant"]):
    """DI marker that resolves to the active `Tenant` for the request."""

    __slots__ = ()


class TenantProvider(RegisteredParameterProvider):
    """Resolve `DTenant` parameters from `request.tenant`."""

    def can_handle(
        self,
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> bool:
        """Match the bare `DTenant` annotation when a request is attached."""
        if param.annotation is not DTenant:
            return False
        request = getattr(context, "request", None)
        if request is None:
            return False
        return get_active_tenant(request) is not None

    def resolve(
        self,
        _param: inspect.Parameter,
        context: ResolutionContext,
    ) -> Tenant:
        """Return the `Tenant` previously stashed by `TenantMiddleware`."""
        return get_active_tenant(context.request)
