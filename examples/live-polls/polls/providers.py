import inspect
from typing import get_args, get_origin

from django.http import Http404

from next.deps import DDependencyBase, RegisteredParameterProvider
from next.deps.context import ResolutionContext


class DPoll[T](DDependencyBase[T]):
    """Annotate a parameter with ``DPoll[Poll]`` to inject the matching row."""

    __slots__ = ()


class PollProvider(RegisteredParameterProvider):
    """Resolve ``DPoll[Model]`` parameters from URL or POST.

    The provider reads ``url_kwargs["id"]`` first. When that is missing
    it falls back to ``request.POST["poll"]`` — the field name used by
    ``VoteForm`` — so any action handler annotated with ``DPoll`` can
    receive the poll through DI without an extra query.
    """

    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Match parameters annotated as a ``DPoll[...]`` subscript."""
        return get_origin(param.annotation) is DPoll

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fetch the poll matching the URL ``id`` or POST ``poll``."""
        (model_cls,) = get_args(param.annotation)
        pk = context.url_kwargs.get("id")
        if pk is None:
            request = getattr(context, "request", None)
            if request is not None:
                pk = request.POST.get("poll")
        if pk is None:
            return None
        try:
            return model_cls.objects.get(pk=pk)
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc
