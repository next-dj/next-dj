"""System checks for the boundary a `page.py` or a `component.py` declares."""

from __future__ import annotations

from django.conf import settings
from django.core.checks import CheckMessage, Error, register

from next.checks import NEXT
from next.components.deferral import COMPONENT_MODULE, defer_declarations
from next.pages.checks.access import load_declarations
from next.pages.compression import compress_declarations
from next.pages.deferral import page_defer_declarations
from next.pages.loaders import PAGE_MODULE


def _page_errors() -> list[CheckMessage]:
    """Report a `page.defer()` or `page.compress()` declaring nothing, misplaced too."""
    errors: list[CheckMessage] = [
        Error(
            f"page.defer() in {file_path} declares nothing, since {problem}. The page "
            "keeps the default options of its boundary.",
            hint="Call page.defer() at the top of the module, with timeout= None or "
            "a duration such as 3 or '3s', and parallel= None, True or False.",
            obj=settings,
            id="next.E222",
        )
        for file_path, declaration in page_defer_declarations.loaded()
        if (problem := declaration.problem()) is not None
    ]
    errors.extend(
        _misplaced("page.defer()", file_path, PAGE_MODULE, "page")
        for file_path in page_defer_declarations.outside()
    )
    errors.extend(
        Error(
            f"page.compress() in {file_path} declares nothing, since {problem}. The "
            "page is compressed as COMPRESSION decides.",
            hint="Call page.compress(False) or page.compress(True) at the top of the "
            "module.",
            obj=settings,
            id="next.E222",
        )
        for file_path, declaration in compress_declarations.loaded()
        if (problem := declaration.problem()) is not None
    )
    errors.extend(
        _misplaced("page.compress()", file_path, PAGE_MODULE, "page")
        for file_path in compress_declarations.outside()
    )
    return errors


def _component_errors() -> list[CheckMessage]:
    """Report every `component.defer()` or `props()` that declares nothing."""
    errors: list[CheckMessage] = [
        Error(
            f"The boundary declaration in {file_path} declares nothing, since "
            f"{problem}. The component keeps the boundary its files give it.",
            hint="Call component.defer() and component.props() at the top of the "
            "module, with the options in the shapes the reference lists.",
            obj=settings,
            id="next.E222",
        )
        for file_path, problem in defer_declarations.problems()
    ]
    errors.extend(
        _misplaced(call, file_path, COMPONENT_MODULE, "component")
        for file_path, call in defer_declarations.outside()
    )
    return errors


def _misplaced(call: str, file_path: object, module: str, owner: str) -> CheckMessage:
    """Build the error of a declaration made outside the module kind it is for."""
    return Error(
        f"{call} in {file_path} declares nothing, since the call sits outside a "
        f"{module} and so names no {owner}.",
        hint=f"Call it at the top of the {module} of the {owner} it is for.",
        obj=settings,
        id="next.E222",
    )


@register(NEXT)
def check_boundary_declarations(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a page or component boundary or compression call declaring nothing."""
    load_declarations()
    return _page_errors() + _component_errors()


__all__ = ["check_boundary_declarations"]
