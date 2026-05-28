from django.db.models import Prefetch, QuerySet
from django.http import HttpRequest
from django.middleware.csrf import get_token
from kanban.models import Board, Card, Column
from kanban.providers import DBoard

from next.forms import form_action_manager
from next.pages import context


@context("board_object", inherit_context=True)
def board_object(active_board: DBoard[Board]) -> Board:
    """Expose the board ORM instance for server-rendered iteration."""
    return active_board


@context("board_url_kwargs", inherit_context=True)
def board_url_kwargs(active_board: DBoard[Board]) -> dict[str, int]:
    """Expose the reverse() kwargs for the current board route.

    Shared by the nav_link calls in the board layout so the tabs link
    back to themselves using `url_name` without hard-coding the URL
    converter on the call site.
    """
    return {"id": active_board.pk}


@context("columns", inherit_context=True)
def columns(active_board: DBoard[Board]) -> QuerySet[Column]:
    """Return the ordered columns of the active board for the template."""
    return active_board.columns.all()


@context("moved_card", inherit_context=True)
def moved_card(request: HttpRequest) -> Card | None:
    """Return the just-moved card when the redirect carried a `?moved=` flag."""
    pk = request.GET.get("moved")
    if not pk:
        return None
    try:
        return Card.objects.select_related("column").get(pk=pk)
    except Card.DoesNotExist:
        return None


@context("board", inherit_context=True, serialize=True)
def board_payload(
    active_board: DBoard[Board],
    request: HttpRequest,
) -> dict[str, object]:
    """Expose the full board tree under the merged board JS context key.

    Drives the React layer at `window.Next.context.board` and includes
    excerpts and wip_limits so client renders match the SSR fallback.
    """
    cols = active_board.columns.prefetch_related(
        Prefetch("cards", queryset=Card.objects.order_by("position"))
    ).order_by("position")
    return {
        "id": active_board.pk,
        "title": active_board.title,
        "slug": active_board.slug,
        "archived": active_board.archived,
        "csrf": get_token(request),
        "move_card_url": form_action_manager.get_action_url("move_card_form"),
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
