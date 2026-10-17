from django.db.models import QuerySet
from django.http import HttpRequest
from django.middleware.csrf import get_token
from kanban.models import Board, Card, Column

from next import context, page
from next.deps import DObject
from next.forms.manager import form_action_manager
from next.pages import MetadataDict


@page.metadata(inherit=True)
def board_meta(board: DObject[Board]) -> MetadataDict:
    """Title the board and its settings page after the resolved board row."""
    return {"title": board.title}


@context("board_object", inherit_context=True)
def board_object(board: DObject[Board]) -> Board:
    """Expose the board ORM instance for server-rendered iteration."""
    return board


@context("board_url_kwargs", inherit_context=True)
def board_url_kwargs(board: DObject[Board]) -> dict[str, int]:
    """Expose the reverse() kwargs for the current board route.

    Shared by the nav_link calls in the board layout so the tabs link back to themselves
    using `url_name` without hard-coding the URL converter on the call site.
    """
    return {"board": board.pk}


@context("columns", inherit_context=True)
def columns(board: DObject[Board]) -> QuerySet[Column]:
    """Return the ordered columns of the active board for the template."""
    return board.columns.all()


@context("moved_card", inherit_context=True)
def moved_card(board: DObject[Board], request: HttpRequest) -> Card | None:
    """Return the card a `?moved=` flag names, when it sits on this board."""
    pk = request.GET.get("moved", "")
    if not (pk.isascii() and pk.isdigit()):
        return None
    return board.card(pk)


@context("board", inherit_context=True, serialize=True)
def board_payload(board: DObject[Board], request: HttpRequest) -> dict[str, object]:
    """Expose the full board tree under the merged board JS context key.

    Drives the React layer at `window.Next.context.board` and includes
    excerpts and wip_limits so client renders match the SSR fallback.
    """
    cols = board.columns.prefetch_related("cards").order_by("position")
    return {
        "id": board.pk,
        "title": board.title,
        "slug": board.slug,
        "archived": board.archived,
        "csrf": get_token(request),
        "move_card_url": form_action_manager.get_action_url("move_card_form"),
        "create_card_url": form_action_manager.get_action_url("create_card_form"),
        "columns": [
            {
                "id": col.id,
                "title": col.title,
                "position": col.position,
                "wip_limit": col.wip_limit,
                "cards": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "position": c.position,
                        "excerpt": c.excerpt,
                    }
                    for c in col.cards.all()
                ],
            }
            for col in cols
        ],
    }
