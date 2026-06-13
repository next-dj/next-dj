import inspect
from typing import get_args, get_origin

from next.deps import DDependencyBase, RegisteredParameterProvider, resolver
from next.deps.context import ResolutionContext

from .cache import get_cached_flag


WRITE_GATE_FLAG = "admin_writes"


class FlagService:
    """Read-only view over the flag cache used by permission hooks."""

    def is_enabled(self, name: str) -> bool:
        """Return True when the named flag exists and is enabled."""
        flag = get_cached_flag(name)
        return bool(flag and flag.enabled)


@resolver.dependency("flag_service")
def flag_service() -> FlagService:
    """Resolve the shared `FlagService` for `Depends("flag_service")` parameters."""
    return FlagService()


class DFlag[T](DDependencyBase[T]):
    """Annotate a parameter with `DFlag[Flag]` to inject the matching `Flag`."""

    __slots__ = ()


class FlagProvider(RegisteredParameterProvider):
    """Resolve `DFlag[...]` parameters by looking up `flag_name`.

    The lookup runs against URL kwargs first, then template context. When the
    flag does not exist yet, a disabled placeholder instance is returned so
    guard components can hide their content without raising.
    """

    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Return True when the parameter annotation is a `DFlag[...]` subscript."""
        return get_origin(param.annotation) is DFlag

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the cached `Flag` for `flag_name`, or a disabled placeholder."""
        (model_cls,) = get_args(param.annotation)
        name = context.url_kwargs.get("name") or context.context_data.get("flag_name")
        if not name:
            msg = "DFlag requires `name` URL kwarg or `flag_name` template context key"
            raise LookupError(msg)
        return get_cached_flag(str(name)) or model_cls(name=str(name), enabled=False)
