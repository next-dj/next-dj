"""The DI provider that hands a metadata callable the fold of its ancestors."""

import inspect
from typing import override

from next.deps import RegisteredParameterProvider, ResolutionContext
from next.deps.markers import unwrap_annotated
from next.deps.plan import ParameterFiller

from .chain import PARENT_KEY
from .schema import EMPTY_METADATA, Metadata


def _parent_metadata(context: ResolutionContext) -> object:
    """Return the parent fold of the resolve under way, or nothing outside one."""
    return context.context_data.get(PARENT_KEY, EMPTY_METADATA)


class ParentMetadataProvider(RegisteredParameterProvider):
    """Fill a parameter annotated `Metadata` with the fold of the chain before it."""

    priority = 25

    @override
    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Defer to the static verdict, which the context never changes."""
        return self.static_can_handle(param)

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Claim the parameter on its annotation alone, ruling every other one out."""
        return unwrap_annotated(param.annotation) is Metadata

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Read the parent fold the metadata resolve published."""
        return _parent_metadata(context)

    @override
    def compile_resolve(self, _param: inspect.Parameter) -> ParameterFiller:
        """Hand the plan the read itself, since nothing about it depends on `param`."""
        return _parent_metadata


__all__ = ["ParentMetadataProvider"]
