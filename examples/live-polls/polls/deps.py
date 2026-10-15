import inspect
from functools import partial
from typing import get_args, get_origin

from django.db.models import Model
from django.http import Http404

from next.deps import DDependencyBase, RegisteredParameterProvider, ResolutionContext
from next.deps.plan import ParameterFiller


class DPoll[T](DDependencyBase[T]):
    """Annotate a parameter with ``DPoll[Poll]`` to inject the matching row."""

    __slots__ = ()


def _by_url_or_post(model_cls: type[Model], context: ResolutionContext) -> Model | None:
    """Fetch the poll named by ``url_kwargs["id"]``, falling back to POST ``poll``.

    The POST field is the one ``VoteForm`` already carries, so an action handler
    reaches the same row as a page render without a query of its own. Both the
    plain ``resolve`` path and the compiled filler land here.
    """
    pk = context.url_kwargs.get("id")
    if pk is None and context.request is not None:
        pk = context.request.POST.get("poll")
    if pk is None:
        return None
    try:
        return model_cls.objects.get(pk=pk)
    except model_cls.DoesNotExist as exc:
        raise Http404 from exc


class PollProvider(RegisteredParameterProvider):
    """Resolve ``DPoll[Model]`` parameters from URL or POST."""

    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Defer to the static verdict, which no resolution context changes."""
        return self.static_can_handle(param)

    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Claim every ``DPoll[...]`` parameter and rule out the others for good.

        The annotation settles the match, so the plan compiler makes this provider
        the terminal one for the parameter instead of asking again per request.
        """
        return get_origin(param.annotation) is DPoll

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fetch the poll matching the URL ``id`` or POST ``poll``."""
        (model_cls,) = get_args(param.annotation)
        return _by_url_or_post(model_cls, context)

    def compile_resolve(self, param: inspect.Parameter) -> ParameterFiller:
        """Read the model off the annotation once, leaving the query to the plan."""
        (model_cls,) = get_args(param.annotation)
        return partial(_by_url_or_post, model_cls)
