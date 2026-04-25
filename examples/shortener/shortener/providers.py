from __future__ import annotations

from typing import TYPE_CHECKING, get_args, get_origin

from django.http import Http404

from next.deps import DDependencyBase, RegisteredParameterProvider


if TYPE_CHECKING:
    import inspect

    from next.deps.context import ResolutionContext


class DLink[T](DDependencyBase[T]):
    """Annotate a parameter with `DLink[Link]` to inject the matching `Link`."""

    __slots__ = ()


class LinkProvider(RegisteredParameterProvider):
    """Resolve `DLink[Model]` parameters by looking up the URL `slug`."""

    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Return True when the parameter annotation is a `DLink[...]` subscript."""
        return get_origin(param.annotation) is DLink

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fetch the model row matching the URL `slug`, or raise `Http404`."""
        (model_cls,) = get_args(param.annotation)
        slug = context.url_kwargs["slug"]
        try:
            return model_cls.objects.get(slug=str(slug))
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc
