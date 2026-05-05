from django.db.models import QuerySet
from kanban.models import Card, Column

from next.components import component


@component.context("cards")
def cards(column: Column) -> QuerySet[Card]:
    """Return the column cards in display order for the template."""
    return column.cards.all()
