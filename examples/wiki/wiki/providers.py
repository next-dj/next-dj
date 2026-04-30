import inspect
from typing import get_args, get_origin

from django.http import Http404

from next.deps import DDependencyBase, RegisteredParameterProvider
from next.deps.context import ResolutionContext


class DArticle[T](DDependencyBase[T]):
    """Annotate a parameter with ``DArticle[Article]`` to inject the matching row."""

    __slots__ = ()


class ArticleProvider(RegisteredParameterProvider):
    """Resolve ``DArticle[Model]`` parameters by looking up the URL ``slug``."""

    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Match parameters annotated as a ``DArticle[...]`` subscript."""
        return get_origin(param.annotation) is DArticle

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fetch the model row matching the URL ``slug``, or raise ``Http404``."""
        (model_cls,) = get_args(param.annotation)
        slug = context.url_kwargs.get("slug")
        if slug is None:
            return None
        try:
            return model_cls.objects.get(slug=str(slug))
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc
