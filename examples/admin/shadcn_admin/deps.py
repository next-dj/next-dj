from django.http import HttpRequest

from next.deps import resolver
from shadcn_admin.forms import AdminFormSpec


@resolver.dependency("admin_spec")
def admin_spec(
    request: HttpRequest, app_label: str, model_name: str, pk: int | None = None
) -> AdminFormSpec:
    """Resolve `(model, ModelAdmin, instance)` once per dispatch.

    Named so `Depends("admin_spec")` shares one resolution across the factory,
    `get_initial`, the action handler, and any re-render context in the same POST.
    """
    return AdminFormSpec.resolve(request, app_label, model_name, pk=pk)
