import inspect
from typing import get_args, get_origin

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import Http404, HttpRequest

from next.deps import (
    DDependencyBase,
    RegisteredParameterProvider,
    ResolutionContext,
    resolver,
)


CARD_PARAM = "card_id"


@resolver.dependency("scope")
def scope(request: HttpRequest) -> AbstractBaseUser | AnonymousUser:
    """Return the visitor, the one value `Board.objects.visible_to` could narrow by."""
    return request.user


class DCard[T](DDependencyBase[T]):
    """Annotate a parameter with ``DCard[Card]`` to inject the matching row."""

    __slots__ = ()


class CardProvider(RegisteredParameterProvider):
    """Resolve ``DCard[Model]`` parameters from a POST ``card_id`` field."""

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Match ``DCard[...]`` annotations when the request carries a card id."""
        if get_origin(param.annotation) is not DCard:
            return False
        request = getattr(context, "request", None)
        if request is None:
            return False
        return bool(request.POST.get(CARD_PARAM))

    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        """Rule out every other annotation and leave the POST check to ``can_handle``.

        A card is owned only while the request carries ``card_id``, so this stays a
        runtime candidate rather than a compiled terminal.
        """
        return None if get_origin(param.annotation) is DCard else False

    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Fetch the card matching POST ``card_id``, or raise ``Http404``."""
        (model_cls,) = get_args(param.annotation)
        pk = context.request.POST.get(CARD_PARAM)
        try:
            return model_cls.objects.select_related("column__board").get(pk=pk)
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc
