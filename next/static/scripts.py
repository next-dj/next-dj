"""Deprecated alias of `next.static.runtime`, kept so an existing import still works.

Every name resolves from `next.static.runtime` with a `DeprecationWarning`, and the
CSRF helpers that moved resolve from `next.csrf`.
"""

import importlib
import warnings
from typing import Final


_MOVED: Final = {"csrf_header_name": "next.csrf", "csrf_payload": "next.csrf"}


def __getattr__(name: str) -> object:
    """Resolve `name` from the module that holds it now, warning once per access."""
    target = _MOVED.get(name, "next.static.runtime")
    module = importlib.import_module(target)
    if not hasattr(module, name):
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    warnings.warn(
        f"next.static.scripts is deprecated, import {name} from {target}",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(module, name)
