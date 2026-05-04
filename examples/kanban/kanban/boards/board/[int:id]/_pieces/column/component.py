from django.db.models import QuerySet
from kanban.models import Card, Column

from next.components import component


@component.context("cards")
def cards(column: Column) -> QuerySet[Card]:
    """Return the column cards in display order for the template."""
    return column.cards.all()


@component.context("board", serialize=True)
def board(column: Column) -> dict[str, object]:
    """Expose column-level state under the merged ``board`` JS context key."""
    board_obj = column.board
    return {
        "columns": [
            {
                "id": col.pk,
                "title": col.title,
                "wip_limit": col.wip_limit,
                "cards": [
                    {"id": card.pk, "title": card.title, "position": card.position}
                    for card in col.cards.all()
                ],
            }
            for col in board_obj.columns.all()
        ],
    }
