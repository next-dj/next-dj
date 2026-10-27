from django.http import HttpRequest
from django.middleware.csrf import get_token

from kanban.models import Board
from kanban.realtime import board_topic
from next.forms.manager import form_action_manager


def board_snapshot(board: Board, request: HttpRequest) -> dict[str, object]:
    """Return the board tree the React island renders, with its action URLs."""
    cols = board.columns.prefetch_related("cards").order_by("position")
    return {
        "id": board.pk,
        "title": board.title,
        "slug": board.slug,
        "archived": board.archived,
        "topic": board_topic(board.pk),
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
