"""System check for the custom patch verbs a project registers."""

import re

from django.core.checks import CheckMessage, Error, Tags, register

from next.checks import NEXT
from next.partial.registry import patch_op_registry

from .codes import E_OP_BAD_NAME


_OP_TOKEN = re.compile(r"\A[A-Za-z0-9_.-]+\Z")


@register(Tags.templates, NEXT)
def check_custom_patch_ops_well_formed(*args, **kwargs) -> list[CheckMessage]:
    """Error when a custom patch verb is not a valid verb token.

    `register_patch_op` already refuses a built-in verb, so only the token shape is
    left to report at `manage.py check` time.
    """
    return [
        Error(
            f'Custom patch op "{name}" is not a valid verb token. A patch verb '
            "travels in the JSON envelope, so use letters, digits, dots, hyphens, "
            "or underscores.",
            id=E_OP_BAD_NAME,
        )
        for name in sorted(patch_op_registry.custom_names())
        if not _OP_TOKEN.match(name)
    ]


__all__ = ["check_custom_patch_ops_well_formed"]
