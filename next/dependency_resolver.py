"""
Dependency Resolver
------------------

Injects dependencies into functions by matching parameter names to available objects.
Used by the framework to supply common values (request, session, user) automatically.

Usage:
    wrapped = dependency_resolver(func, available_deps)
    result = wrapped(*args, **kwargs)
"""

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any


def dependency_resolver(func: Callable, available_deps: dict[str, Any]) -> Callable:
    """
    Wraps a function to inject dependencies by name.

    This function takes any callable and a dictionary of available dependencies,
    then returns a new function that automatically fills missing parameters
    from the dependency dictionary. This is super useful for dependency injection
    where you want to automatically provide things like request, user, session, etc.

    Args:
        func: The function to wrap with dependency injection
        available_deps: Dictionary mapping parameter names to their values

    Returns:
        A new function that will inject dependencies when called
    """

    sig = inspect.signature(func)
    param_names = list(sig.parameters.keys())

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        bound_args = sig.bind_partial(*args, **kwargs)
        bound_args.apply_defaults()
        for name in param_names:
            if name not in bound_args.arguments and name in available_deps:
                bound_args.arguments[name] = available_deps[name]()
        return func(*bound_args.args, **bound_args.kwargs)
    return wrapper
