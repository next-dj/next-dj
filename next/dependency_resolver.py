"""
Dependency Resolver
------------------

Injects dependencies into functions by matching parameter names to available objects.
Used by the framework to supply common values (request, session, user) automatically.

Usage:
    wrapped = dependency_resolver(func, available_deps)
    result = wrapped(*args, **kwargs)
"""

def dependency_resolver(func, available_deps):
    """
    Wraps a function to inject dependencies by name.
    Fills missing parameters from available_deps.
    """
    import inspect
    from functools import wraps

    sig = inspect.signature(func)
    param_names = list(sig.parameters.keys())

    @wraps(func)
    def wrapper(*args, **kwargs):
        bound_args = sig.bind_partial(*args, **kwargs)
        bound_args.apply_defaults()
        for name in param_names:
            if name not in bound_args.arguments and name in available_deps:
                bound_args.arguments[name] = available_deps[name]()
        return func(*bound_args.args, **bound_args.kwargs)
    return wrapper
