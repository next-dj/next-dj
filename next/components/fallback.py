"""The error a component or a page boundary settles as, and whether it can retry.

It reads the static manager, so nothing the components package imports may import it.
"""

from next.boundaries import new_digest, shown_kind
from next.conf.defer import defer_option
from next.defer import BoundaryError, ErrorKind
from next.static import ScriptInjectionPolicy, get_static_manager


def retry_loads() -> bool:
    """Return whether the runtime script loads, which every retry needs."""
    policy = get_static_manager().script_builder().policy
    return policy is not ScriptInjectionPolicy.DISABLED


def unrun_error(kind: ErrorKind) -> BoundaryError:
    """Return the error a boundary settles as without running, under its shown kind."""
    shown = shown_kind(kind, opaque=defer_option("ERRORS", "KINDS") == "opaque")
    return BoundaryError.of(
        shown, new_digest(), retry=retry_loads(), message="", traceback=""
    )


__all__ = ["retry_loads", "unrun_error"]
