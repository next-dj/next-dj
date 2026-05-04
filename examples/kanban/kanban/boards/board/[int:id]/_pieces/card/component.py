from kanban.models import Card

from next.components import component


_EXCERPT_LIMIT = 100


@component.context("excerpt")
def excerpt(card: Card) -> str:
    """Trim the card body for in-card preview."""
    text = card.body or ""
    if len(text) <= _EXCERPT_LIMIT:
        return text
    return text[: _EXCERPT_LIMIT - 1].rstrip() + "…"
