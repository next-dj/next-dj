import logging

from django.dispatch import receiver

from next.signals import form_access_denied


logger = logging.getLogger("notes.access")


@receiver(form_access_denied)
def _on_form_access_denied(
    action_name: str,
    layer: str,
    reason: str,
    request: object = None,
    **_: object,
) -> None:
    """Log a permission-hook denial, naming the tenant when the request carries one."""
    tenant = getattr(request, "tenant", None)
    tenant_slug = getattr(tenant, "slug", "unknown")
    logger.warning(
        "form access denied action=%s layer=%s reason=%s tenant=%s",
        action_name,
        layer,
        reason,
        tenant_slug,
    )
