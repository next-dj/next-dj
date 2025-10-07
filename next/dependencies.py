"""
Dependency injection for context and render functions.

Supports injection by type hints and parameter names to reduce boilerplate
when writing page handlers that need request, session, or user objects.
"""

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints


class DependencyResolver:
    """
    Resolves and injects dependencies into functions based on type hints.

    Caches resolution per function to avoid repeated introspection overhead.
    """

    def __init__(self):
        self._injection_maps: dict[str, dict[str, str]] = {}
        self._result_cache: dict[str, dict[str, Any]] = {}

    def resolve(
        self,
        func: Callable,
        deps: dict[str, Any],
        explicit: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Build kwargs dict for func by inspecting signature and injecting deps.

        Explicit kwargs override any injected values. Only injects parameters
        that exist in the function signature and match known patterns.
        """
        cache_key = f"{func.__module__}.{func.__qualname__}"

        # return cached result if available and no explicit overrides
        if not explicit and cache_key in self._result_cache:
            return self._result_cache[cache_key]

        if cache_key not in self._injection_maps:
            self._injection_maps[cache_key] = self._build_injection_map(func)

        injection_map = self._injection_maps[cache_key]
        result = {}

        for param_name, dep_key in injection_map.items():
            if param_name in explicit:
                result[param_name] = explicit[param_name]
            elif dep_key in deps:
                result[param_name] = deps[dep_key]

        # add any explicit kwargs that aren't in the injection map
        for key, value in explicit.items():
            if key not in result:
                result[key] = value

        # cache result only if no explicit overrides were provided
        if not explicit:
            self._result_cache[cache_key] = result

        return result

    def _build_injection_map(self, func: Callable) -> dict[str, str]:
        """
        Determine which parameters should receive which dependencies.

        Returns a mapping of parameter name to dependency key.
        """
        sig = inspect.signature(func)
        hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
        injection_map = {}

        for param_name, param in sig.parameters.items():
            # skip *args, **kwargs, and self/cls
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue

            # check for type-based injection
            if param_name in hints:
                hint = hints[param_name]
                hint_name = getattr(hint, "__name__", None)

                if hint_name == "HttpRequest":
                    injection_map[param_name] = "request"
                elif hint_name == "SessionBase":
                    injection_map[param_name] = "session"

            # check for name-based injection (user)
            elif param_name == "user":
                injection_map[param_name] = "user"

        return injection_map
