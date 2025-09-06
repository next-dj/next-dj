import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.template import Context, Template


class Page:
    """
    Сollects Django template blocks from code and stores them for later processing.

    Django Extension (DJX) is a common class for registering global templates and context functions
    for page files (like page.py) in all registered Django applications.
    """

    def __init__(self) -> None:
        self._template_registry: dict[Path, str] = {}
        self._context_registry: dict[Path, dict[str | None, Callable[..., Any]]] = {}

    def register_template(self, file_path: Path, template_str: str) -> None:
        """
        Register template from page.djx.py file.

        It should be a string of the template. Only one template is allowed per file.

        Automatically detects the file path.
        """
        self._template_registry[file_path] = template_str

    def _get_caller_path(self, back_count: int = 1) -> Path:
        """Get the file path of the caller at the specified stack depth."""
        frame = inspect.currentframe()
        for _ in range(back_count):
            if frame is None or frame.f_back is None:
                raise RuntimeError("Could not determine caller file path")
            frame = frame.f_back

        # look for the actual module file, not the decorator file
        max_frames = 10  # limit to prevent infinite loop
        frame_count = 0

        while frame is not None and frame_count < max_frames:
            file_path = frame.f_globals.get("__file__")
            if file_path is not None and not file_path.endswith("pages.py"):
                return Path(file_path)
            frame = frame.f_back
            frame_count += 1

        raise RuntimeError("Could not determine caller file path")

    def context(
        self, func_or_key: Any = None
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """
        Register context function for the current file.

        Allows two ways to register context functions:
        - @djx.context("key") - wraps the function result in a dict with the given key
        - @djx.context - expects the function to return a dictionary directly

        Automatically detects the file path.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(func_or_key):
                # when called as @context, we need to go back 2 frames
                caller_path = self._get_caller_path(2)
                # no key provided, expect function to return dict
                self._context_registry.setdefault(caller_path, {})[None] = func_or_key
            else:
                # when called as @context("key"), we need to go back 1 frame
                caller_path = self._get_caller_path(1)
                self._context_registry.setdefault(caller_path, {})[func_or_key] = func

            return func

        # handle case where context is called without parentheses: @djx.context
        if callable(func_or_key):
            return decorator(func_or_key)

        return decorator

    def render(self, file_path: Path, *args: Any, **kwargs: Any) -> str:
        """Render the template registered for the given file_path."""
        template_str = self._template_registry[file_path]

        # collect context from registered context functions
        context_data = {}
        for key, func in self._context_registry.get(file_path, {}).items():
            if key is None:
                context_data.update(func(*args, **kwargs))
            else:
                context_data[key] = func(*args, **kwargs)

        # add kwargs to context data (template variables override context functions)
        context_data.update(kwargs)

        return Template(template_str).render(Context(context_data))


# global instance for use throughout the project
page = Page()

# alias for page.context
context = page.context
