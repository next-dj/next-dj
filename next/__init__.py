r"""                  __           __
                     /\ \__       /\ \    __
  ___      __   __  _\ \ ,_\      \_\ \  /\_\
/' _ `\  /'__`\/\ \/'\\ \ \/      /'_` \ \/\ \
/\ \/\ \/\  __/\/>  </ \ \ \_  __/\ \L\ \ \ \ \
\ \_\ \_\ \____\/\_/\_\ \ \__\/\_\ \___,_\_\ \ \
 \/_/\/_/\/____/\//\/_/  \/__/\/_/\/__,_ /\ \_\ \
                                         \ \____/
                                          \/___/

A next-gen framework based on Django without the tears.
"""

# Every annotation here is a builtin, so the module skips
# `from __future__ import annotations` and the import it would add to `import next`.
import importlib
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from next.components import component
    from next.deps import Depends
    from next.forms import action
    from next.pages import context, page


__all__ = ["VERSION", "Depends", "action", "component", "context", "page"]

__title__ = "Next Django Framework"
__version__ = "0.8.0"
__author__ = "paqstd-dev"

VERSION = __version__


_LAZY_ATTRIBUTES: dict[str, str] = {
    "page": "next.pages",
    "context": "next.pages",
    "component": "next.components",
    "action": "next.forms",
    "Depends": "next.deps",
}


def _resolve(name: str) -> object:
    """Resolve a curated top-level name from its owning subpackage on demand.

    Importing any subpackage pulls in Django, so the facade stays lazy to keep
    `import next` free of Django imports.
    """
    module_name = _LAZY_ATTRIBUTES.get(name)
    if module_name is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return getattr(importlib.import_module(module_name), name)


# A visible module `__getattr__` would make any name typecheck, so only the binding
# hides from type checkers and `_resolve` stays outside the guard to keep its body
# checked.
if not TYPE_CHECKING:
    __getattr__ = _resolve


def __dir__() -> list[str]:
    """List the curated names alongside the live module namespace."""
    return sorted(set(__all__) | set(globals()))
