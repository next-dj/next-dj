"""System checks for the boundary a `page.py` or a `component.py` declares."""

from __future__ import annotations

from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, register

from next.checks import NEXT
from next.checks.common import DeclaringCall, declaration_errors, misplaced_error
from next.components.defer import COMPONENT_MODULE, defer_declarations
from next.pages.checks.access import load_declarations
from next.pages.compression import compress_store
from next.pages.defer import page_defer_store
from next.pages.loaders import PAGE_MODULE


_CODE: Final = "next.E222"
_DEFER: Final = DeclaringCall("page.defer()", _CODE, PAGE_MODULE, "page")
_COMPRESS: Final = DeclaringCall("page.compress()", _CODE, PAGE_MODULE, "page")


def _page_errors() -> list[CheckMessage]:
    """Report a `page.defer()` or `page.compress()` declaring nothing, misplaced too."""
    errors = declaration_errors(
        _DEFER,
        page_defer_store,
        consequence="The page keeps the default options of its boundary.",
        hint="Call page.defer() at the top of the module, with timeout= None or a "
        "duration such as 3 or '3s', and parallel= None, True or False.",
    )
    errors.extend(
        declaration_errors(
            _COMPRESS,
            compress_store,
            consequence="The page is compressed as COMPRESSION decides.",
            hint="Call page.compress(False) or page.compress(True) at the top of the "
            "module.",
        )
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
            id=_CODE,
        )
        for file_path, problem in defer_declarations.problems()
    ]
    errors.extend(
        misplaced_error(
            DeclaringCall(call, _CODE, COMPONENT_MODULE, "component"), file_path
        )
        for file_path, call in defer_declarations.outside()
    )
    return errors


@register(NEXT)
def check_boundary_declarations(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a page or component boundary or compression call declaring nothing."""
    load_declarations()
    return _page_errors() + _component_errors()


__all__ = ["check_boundary_declarations"]
