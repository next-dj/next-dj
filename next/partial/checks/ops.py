"""System check for the custom patch verbs a project registers.

The ids are `next.E066` for a shadowed built-in and `next.E090` for a bad verb name.
"""

import re

from django.core.checks import CheckMessage, Error, Tags, register

from next.checks import NEXT
from next.partial.registry import BUILTIN_OPS, patch_op_registry

from .codes import E_OP_BAD_NAME, E_OP_SHADOWS_BUILTIN


_OP_TOKEN = re.compile(r"\A[A-Za-z0-9_.-]+\Z")


@register(Tags.templates, NEXT)
def check_custom_patch_ops_well_formed(*args, **kwargs) -> list[CheckMessage]:
    """Error when a custom patch verb is malformed or shadows a built-in.

    Mirrors the runtime guard in `Patches.op()` at startup, so a bad verb name is caught
    at `manage.py check` time instead of only when an op of that name reaches a client.
    """
    messages: list[CheckMessage] = []
    for name in sorted(patch_op_registry.custom_names()):
        if name in BUILTIN_OPS:
            messages.append(
                Error(
                    f'Custom patch op "{name}" shadows a built-in verb. The '
                    "built-in verb wins on the wire, so the custom handler "
                    "never runs. Register the op under a different name.",
                    id=E_OP_SHADOWS_BUILTIN,
                )
            )
            continue
        if not _OP_TOKEN.match(name):
            messages.append(
                Error(
                    f'Custom patch op "{name}" is not a valid verb token. A '
                    "patch verb travels in the JSON envelope, so use letters, "
                    "digits, dots, hyphens, or underscores.",
                    id=E_OP_BAD_NAME,
                )
            )
    return messages


__all__ = ["check_custom_patch_ops_well_formed"]
