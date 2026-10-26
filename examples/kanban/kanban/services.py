from django.db import transaction

from kanban.models import Card, Column


def move_card(card: Card, target_column: Column, target_position: int) -> Card:
    """Detach the card and re-insert it at the requested slot, renumbering both."""
    with transaction.atomic():
        source_column = card.column
        siblings = list(
            source_column.cards.exclude(pk=card.pk).order_by("position", "id")
        )
        for index, sibling in enumerate(siblings):
            if sibling.position != index:
                sibling.position = index
                sibling.save(update_fields=["position"])
        targets = list(
            target_column.cards.exclude(pk=card.pk).order_by("position", "id")
        )
        position = min(target_position, len(targets))
        targets.insert(position, card)
        for index, sibling in enumerate(targets):
            if sibling.pk == card.pk:
                card.column = target_column
                card.position = index
                card.save(update_fields=["column", "position"])
            elif sibling.position != index:
                sibling.position = index
                sibling.save(update_fields=["position"])
    return card
