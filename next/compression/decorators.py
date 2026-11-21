"""The view decorator that keeps the responses of a plain Django view uncompressed."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import cast

from asgiref.sync import iscoroutinefunction
from django.http.response import HttpResponseBase

from .manager import OPT_OUT


def _kept(response: HttpResponseBase) -> HttpResponseBase:
    setattr(response, OPT_OUT, False)
    return response


def no_compress[V: Callable[..., object]](view: V) -> V:
    """Mark every response of `view` so no next.dj compression encodes it.

    It is the opt-out of a view that renders a secret beside reflected input.
    """
    if iscoroutinefunction(view):
        source = cast("Callable[..., Awaitable[HttpResponseBase]]", view)

        @wraps(source)
        async def aview(*args: object, **kwargs: object) -> HttpResponseBase:
            return _kept(await source(*args, **kwargs))

        return cast("V", aview)
    plain = cast("Callable[..., HttpResponseBase]", view)

    @wraps(plain)
    def sync_view(*args: object, **kwargs: object) -> HttpResponseBase:
        return _kept(plain(*args, **kwargs))

    return cast("V", sync_view)


__all__ = ["no_compress"]
