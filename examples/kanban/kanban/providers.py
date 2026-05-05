import inspect
from typing import get_args, get_origin

from django.http import Http404

from next.deps import DDependencyBase, RegisteredParameterProvider
from next.deps.context import ResolutionContext


class DBoard[T](DDependencyBase[T]):
    """Annotate a parameter with ``DBoard[Board]`` to inject the matching row."""

    __slots__ = ()


class DCard[T](DDependencyBase[T]):
    """Annotate a parameter with ``DCard[Card]`` to inject the matching row."""

    __slots__ = ()


class BoardProvider(RegisteredParameterProvider):
    """Resolve ``DBoard[Model]`` parameters from URL or POST.

    Checks ``url_kwargs["id"]`` first, then falls back to a POST
    ``board_id`` field so form actions can receive a board through DI
    without re-fetching it inside the handler.
    """

    def can_handle(
        self,
        param: inspect.Parameter,
        _context: ResolutionContext,
    ) -> bool:
        """Match parameters annotated as a ``DBoard[...]`` subscript."""
        return get_origin(param.annotation) is DBoard

    def resolve(
        self,
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> object:
        """Fetch the board matching the URL ``id`` or POST ``board_id``."""
        (model_cls,) = get_args(param.annotation)
        pk = context.url_kwargs.get("id")
        if pk is None:
            request = getattr(context, "request", None)
            if request is not None:
                pk = request.POST.get("board_id")
        if pk is None:
            return None
        try:
            return model_cls.objects.get(pk=pk)
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc


class CardProvider(RegisteredParameterProvider):
    """Resolve ``DCard[Model]`` parameters from a POST ``card_id`` field."""

    def can_handle(
        self,
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> bool:
        """Match ``DCard[...]`` annotations when the request carries ``card_id``."""
        if get_origin(param.annotation) is not DCard:
            return False
        request = getattr(context, "request", None)
        if request is None:
            return False
        return bool(request.POST.get("card_id"))

    def resolve(
        self,
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> object:
        """Fetch the card matching POST ``card_id``, or raise ``Http404``."""
        (model_cls,) = get_args(param.annotation)
        pk = context.request.POST.get("card_id")
        try:
            return model_cls.objects.select_related("column__board").get(pk=pk)
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc
