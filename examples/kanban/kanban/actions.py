from __future__ import annotations

from django.db import transaction
from django.http import HttpRequest, HttpResponseRedirect

from kanban.forms import (
    ArchiveBoardForm,
    CreateCardForm,
    CreateColumnForm,
    MoveCardForm,
    RenameBoardForm,
)
from kanban.models import Board, Card
from next.forms import action


_NAMESPACE = "kanban"


@action("move_card", namespace=_NAMESPACE, form_class=MoveCardForm)
def move_card(form: MoveCardForm, request: HttpRequest) -> HttpResponseRedirect:
    """Detach the card and re-insert it at the requested position."""
    del request
    card = form.cleaned_data["_card"]
    target_column = form.cleaned_data["_target_column"]
    target_position = form.cleaned_data["target_position"]
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
    return HttpResponseRedirect(f"/board/{target_column.board_id}/?moved={card.pk}")


@action("create_card", namespace=_NAMESPACE, form_class=CreateCardForm)
def create_card(form: CreateCardForm, request: HttpRequest) -> HttpResponseRedirect:
    """Append a card at the tail of the target column."""
    del request
    column = form.cleaned_data["_column"]
    next_position = column.cards.count()
    Card.objects.create(
        column=column,
        title=form.cleaned_data["title"],
        body=form.cleaned_data.get("body", ""),
        position=next_position,
    )
    return HttpResponseRedirect(f"/board/{column.board_id}/")


@action("create_column", namespace=_NAMESPACE, form_class=CreateColumnForm)
def create_column(
    form: CreateColumnForm,
    request: HttpRequest,
) -> HttpResponseRedirect:
    """Append a column to the board at the next free position."""
    del request
    board_id = form.cleaned_data["board_id"]
    board = Board.objects.get(pk=board_id)
    next_position = board.columns.count()
    board.columns.create(
        title=form.cleaned_data["title"],
        position=next_position,
        wip_limit=form.cleaned_data.get("wip_limit"),
    )
    return HttpResponseRedirect(f"/board/{board_id}/settings/")


@action("rename_board", namespace=_NAMESPACE, form_class=RenameBoardForm)
def rename_board(
    form: RenameBoardForm,
    request: HttpRequest,
) -> HttpResponseRedirect:
    """Update the board title while keeping its slug stable."""
    del request
    board_id = form.cleaned_data["board_id"]
    board = Board.objects.get(pk=board_id)
    board.title = form.cleaned_data["title"]
    board.save(update_fields=["title"])
    return HttpResponseRedirect(f"/board/{board_id}/settings/")


@action("archive_board", namespace=_NAMESPACE, form_class=ArchiveBoardForm)
def archive_board(
    form: ArchiveBoardForm,
    request: HttpRequest,
) -> HttpResponseRedirect:
    """Toggle the archived flag on the board."""
    del request
    board_id = form.cleaned_data["board_id"]
    board = Board.objects.get(pk=board_id)
    board.archived = bool(form.cleaned_data["archived"])
    board.save(update_fields=["archived"])
    if board.archived:
        return HttpResponseRedirect("/")
    return HttpResponseRedirect(f"/board/{board_id}/settings/")
