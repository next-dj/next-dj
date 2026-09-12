import inspect
from functools import partial
from typing import get_args, get_origin

from django.db.models import Model
from django.http import Http404

from next.deps import DDependencyBase, RegisteredParameterProvider, ResolutionContext
from next.deps.plan import ParameterFiller


class DBoard[T](DDependencyBase[T]):
    """Annotate a parameter with ``DBoard[Board]`` to inject the matching row."""

    __slots__ = ()


class DCard[T](DDependencyBase[T]):
    """Annotate a parameter with ``DCard[Card]`` to inject the matching row."""

    __slots__ = ()


def _fetch_board(model_cls: type[Model], context: ResolutionContext) -> object:
    """Fetch the board keyed by the URL ``id``, then by a POST ``board_id``.

    Shared by ``resolve`` and the compiled filler, so the two paths cannot drift
    apart and the only difference between them is when the annotation is read.
    """
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


class BoardProvider(RegisteredParameterProvider):
    """Resolve ``DBoard[Model]`` parameters from URL or POST.

    Checks ``url_kwargs["id"]`` first, then falls back to a POST
    ``board_id`` field so form actions can receive a board through DI
    without re-fetching it inside the handler.
    """

    def can_handle(self, param: inspect.Parameter, _context: ResolutionContext) -> bool:
        """Defer to the static verdict, which the context never changes."""
        return self.static_can_handle(param)

    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Settle on the annotation alone, so the plan claims the parameter for good.

        A ``DBoard[...]`` parameter is owned here in every context, which makes this
        provider the terminal of its plan entry and skips the per-request walk.
        """
        return get_origin(param.annotation) is DBoard

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fetch the board matching the URL ``id`` or POST ``board_id``."""
        (model_cls,) = get_args(param.annotation)
        return _fetch_board(model_cls, context)

    def compile_resolve(self, param: inspect.Parameter) -> ParameterFiller:
        """Read the model off the annotation once per plan, not once per resolve."""
        (model_cls,) = get_args(param.annotation)
        return partial(_fetch_board, model_cls)


class CardProvider(RegisteredParameterProvider):
    """Resolve ``DCard[Model]`` parameters from a POST ``card_id`` field."""

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Match ``DCard[...]`` annotations when the request carries ``card_id``."""
        if get_origin(param.annotation) is not DCard:
            return False
        request = getattr(context, "request", None)
        if request is None:
            return False
        return bool(request.POST.get("card_id"))

    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        """Rule out every other annotation and leave the POST check to ``can_handle``.

        A card is owned only while the request carries ``card_id``, so the plan
        keeps this provider a runtime candidate rather than a terminal, and the
        compiler never asks a candidate for a compiled filler.
        """
        return None if get_origin(param.annotation) is DCard else False

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fetch the card matching POST ``card_id``, or raise ``Http404``."""
        (model_cls,) = get_args(param.annotation)
        pk = context.request.POST.get("card_id")
        try:
            return model_cls.objects.select_related("column__board").get(pk=pk)
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc
