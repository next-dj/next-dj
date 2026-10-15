import inspect
from functools import partial
from typing import get_args, get_origin

from django.db.models import Model
from django.http import Http404

from next.deps import DDependencyBase, RegisteredParameterProvider, ResolutionContext
from next.deps.plan import ParameterFiller


class DArticle[T](DDependencyBase[T]):
    """Annotate a parameter with ``DArticle[Article]`` to inject the matching row."""

    __slots__ = ()


def _by_url_slug(model_cls: type[Model], context: ResolutionContext) -> Model | None:
    """Fetch the row named by the URL ``slug``, or ``None`` when no slug is captured.

    Both the plain ``resolve`` path and the compiled filler land here, so the
    lookup and its two empty answers are written once for the two of them.
    """
    slug = context.url_kwargs.get("slug")
    if slug is None:
        return None
    try:
        return model_cls.objects.get(slug=str(slug))
    except model_cls.DoesNotExist as exc:
        raise Http404 from exc


class ArticleProvider(RegisteredParameterProvider):
    """Resolve ``DArticle[Model]`` parameters by looking up the URL ``slug``."""

    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Defer to the static verdict, which no resolution context changes."""
        return self.static_can_handle(param)

    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Claim every ``DArticle[...]`` parameter and rule out the others for good.

        The annotation settles the match, so the plan compiler makes this provider
        the terminal one for the parameter instead of asking again per request.
        """
        return get_origin(param.annotation) is DArticle

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fetch the model row matching the URL ``slug``, or raise ``Http404``."""
        (model_cls,) = get_args(param.annotation)
        return _by_url_slug(model_cls, context)

    def compile_resolve(self, param: inspect.Parameter) -> ParameterFiller:
        """Read the model off the annotation once, leaving the query to the plan."""
        (model_cls,) = get_args(param.annotation)
        return partial(_by_url_slug, model_cls)
