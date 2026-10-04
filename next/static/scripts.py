"""Deprecated alias of `next.static.runtime`, kept so an existing import still works.

Every name resolves from `next.static.runtime` with a `DeprecationWarning`, and the
CSRF helpers resolve from `next.csrf` under their current names.
"""

import importlib
import warnings
from typing import Final


_MOVED: Final = {
    "csrf_header_name": ("next.csrf", "csrf_header_name"),
    "csrf_payload": ("next.csrf", "csrf_token_payload"),
}


def __getattr__(name: str) -> object:
    """Resolve `name` from the module that defines it, warning on every access."""
    target, current = _MOVED.get(name, ("next.static.runtime", name))
    module = importlib.import_module(target)
    if not hasattr(module, current):
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    warnings.warn(
        f"next.static.scripts is deprecated, import {current} from {target}",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(module, current)
