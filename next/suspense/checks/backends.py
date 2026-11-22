"""The check of the `DEFER` paths that name a backend, a token seam or a callable."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from django.core.checks import CheckMessage, register

from next.checks import NEXT
from next.checks.common import (
    callable_problem,
    class_problem,
    import_backend_class,
    raw_scope,
)
from next.conf.checks import error_for_unusable_path
from next.conf.defer import SCOPE
from next.suspense.backends import Admission
from next.suspense.executors import BoundaryExecutor
from next.suspense.policy import StreamPolicy
from next.suspense.tokens import (
    BoundaryTokenBackend,
    TokenCodec,
    TokenSeal,
    TokenStorage,
    TokenTransport,
)
from next.suspense.tokens.binding import BINDINGS, binding_problem
from next.suspense.transport import StreamTransport

from .settings import shipped, value_at, where


@dataclass(frozen=True, slots=True)
class _Family:
    """One `DEFER` key that names a backend class and the root the class extends."""

    path: tuple[str, ...]
    root: type


_FAMILIES: Final = (
    _Family(("EXECUTOR", "BACKEND"), BoundaryExecutor),
    _Family(("ADMISSION",), Admission),
    _Family(("POLICY", "BACKEND"), StreamPolicy),
    _Family(("TRANSPORT", "BACKEND"), StreamTransport),
    _Family(("TOKENS", "BACKEND"), BoundaryTokenBackend),
)

_SEAMS: Final = (
    _Family(("TOKENS", "OPTIONS", "CODEC"), TokenCodec),
    _Family(("TOKENS", "OPTIONS", "STORAGE"), TokenStorage),
    _Family(("TOKENS", "OPTIONS", "SEAL"), TokenSeal),
    _Family(("TOKENS", "OPTIONS", "TRANSPORT"), TokenTransport),
)
"""The token seams of the shipped backend, read while no other backend owns them."""

_CALLABLES: Final = (("TOKENS", "OPTIONS", "KEYS"), ("TOKENS", "OPTIONS", "MAX_AGE"))
_BINDING: Final = ("TOKENS", "OPTIONS", "BINDING")
_CALLABLE_HINT: Final = "Name an importable callable."
_BINDING_HINT: Final = (
    'Name "none", "session", "user", a SubjectBinding subclass that takes no '
    "arguments, or a request callable that returns the subject as text or None."
)
_UNMINTED: Final = "no boundary token can be minted"


def _binding(dotted: str) -> str | None:
    """Return why `dotted` names neither a binding nor a callable, `None` for one."""
    if dotted in BINDINGS:
        return None
    try:
        target: object = import_backend_class(dotted)
    except ImportError as exc:
        return f"does not import ({exc})"
    return binding_problem(target)


def _family_error(
    scope: Mapping[str, object],
    family: _Family,
    consequence: str = "no deferred job can run",
) -> CheckMessage | None:
    """Build the `next.E219` error for one family, `None` for a usable path."""
    dotted = value_at(scope, family.path)
    if not isinstance(dotted, str) or not dotted:
        return None
    problem = class_problem(dotted, family.root)
    if problem is None:
        return None
    return error_for_unusable_path(
        where(family.path),
        dotted,
        problem,
        consequence,
        f"Name a concrete {family.root.__name__} subclass, such as "
        f"{shipped(family.path)!r}.",
    )


def _shipped_tokens(scope: Mapping[str, object]) -> bool:
    """Whether the shipped token backend owns the `TOKENS` options as written."""
    tokens = scope.get("TOKENS")
    backend = tokens.get("BACKEND") if isinstance(tokens, Mapping) else None
    return backend in {None, shipped(("TOKENS", "BACKEND"))}


_PROBES: Final[tuple[tuple[tuple[str, ...], Callable[[str], str | None], str], ...]] = (
    *((path, callable_problem, _CALLABLE_HINT) for path in _CALLABLES),
    (_BINDING, _binding, _BINDING_HINT),
)


def _option_errors(scope: Mapping[str, object]) -> list[CheckMessage]:
    """Build the `next.E219` errors for the token options that name a path."""
    errors = [_family_error(scope, family, _UNMINTED) for family in _SEAMS]
    for path, probe, hint in _PROBES:
        dotted = value_at(scope, path)
        problem = probe(dotted) if isinstance(dotted, str) and dotted else None
        if problem is not None:
            errors.append(
                error_for_unusable_path(
                    where(path), str(dotted), problem, _UNMINTED, hint
                )
            )
    return [error for error in errors if error is not None]


@register(NEXT)
def check_defer_backends(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `DEFER` backend path that names no usable backend (`next.E219`)."""
    scope = raw_scope(SCOPE)
    if scope is None:
        return []
    found = [_family_error(scope, family) for family in _FAMILIES]
    errors = [error for error in found if error is not None]
    if _shipped_tokens(scope):
        errors.extend(_option_errors(scope))
    return errors


__all__ = ["check_defer_backends"]
