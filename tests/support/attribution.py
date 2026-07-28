import functools
from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponse


def wraps_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``func`` with ``functools.wraps``, keeping the ``__wrapped__`` link."""

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> object:
        return func(*args, **kwargs)

    return wrapper


def unwrapped_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``func`` without ``functools.wraps``, leaving no ``__wrapped__`` link."""

    def wrapper(*args: object, **kwargs: object) -> object:
        return func(*args, **kwargs)

    return wrapper


def handler_declared_here(request: HttpRequest) -> HttpResponse:
    """Answer with the request method, from a module that never applies ``@action``."""
    return HttpResponse(request.method)
