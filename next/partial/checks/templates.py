"""System check that every composed page template compiles.

The id is `next.E072`, and compiling the whole tree makes it a deployment check.
"""

from typing import TYPE_CHECKING

from django.core.checks import CheckMessage, Error, Tags, register
from django.template import TemplateDoesNotExist, TemplateSyntaxError

from next.checks import NEXT
from next.checks.common import first_visit, get_router_manager, iter_scanned_page_pairs
from next.pages import page

from .codes import E_COMPOSED_TEMPLATE_SYNTAX


if TYPE_CHECKING:
    from pathlib import Path


@register(Tags.templates, NEXT, deploy=True)
def check_composed_templates_compile(*args, **kwargs) -> list[CheckMessage]:
    """Error when a composed page template fails to compile (`next.E072`).

    The zone checks skip a page whose composed template does not compile, so without
    this check the syntax error would surface only as a 500 on the first request.
    """
    messages: list[CheckMessage] = []
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return messages
    seen: set[Path] = set()
    for router in router_manager.backends:
        for _url_path, page_path in iter_scanned_page_pairs(router):
            if not first_visit(page_path, seen) or not page.has_template(page_path):
                continue
            try:
                page.composed_template_for(page_path)
            except TemplateSyntaxError as error:
                messages.append(
                    Error(
                        f"The composed page template for {page_path} does not "
                        f"compile. {error}",
                        obj=str(page_path),
                        id=E_COMPOSED_TEMPLATE_SYNTAX,
                    )
                )
            except (TemplateDoesNotExist, OSError, ValueError):
                continue
    return messages


__all__ = ["check_composed_templates_compile"]
