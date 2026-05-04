from django.db.models import QuerySet
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
    """Expose board metadata under the merged ``board`` JS context key."""
    return {
        "id": active_board.pk,
        "title": active_board.title,
        "slug": active_board.slug,
        "archived": active_board.archived,
        "csrf": get_token(request),
        "move_card_url": form_action_manager.get_action_url("kanban:move_card"),
    }
