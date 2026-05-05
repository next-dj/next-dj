from kanban.models import Card

from next.components import component


@component.context("excerpt")
def excerpt(card: Card) -> str:
    """Trim the card body for in-card preview."""
    return card.excerpt
