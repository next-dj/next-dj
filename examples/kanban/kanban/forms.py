from typing import ClassVar

from django import forms as django_forms
from django.db import transaction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)

from kanban.models import Board, Card, Column
from kanban.providers import CARD_PARAM, DBoard, DCard
from next.forms import ComponentWidget, Form, ModelForm
from next.urls import page_reverse, with_query


BOARD_PAGE = "board/[int:id]"
BOARD_SETTINGS_PAGE = "board/[int:id]/settings"


def _is_full(column: Column) -> bool:
    """Return whether the column already holds as many cards as its limit allows."""
    return column.wip_limit is not None and column.cards.count() >= column.wip_limit


class MoveCardForm(Form):
    """Move a card to a target column at a chosen position.

    `CardProvider` resolves the moved card from the POST, and the target column is
    narrowed to that card's own board, so a column on another board never validates.
    """

    target_column = django_forms.ModelChoiceField(
        queryset=Column.objects.none(), widget=django_forms.HiddenInput
    )
    target_position = django_forms.IntegerField(
        min_value=0, widget=django_forms.HiddenInput
    )

    def __init__(self, *args, **kwargs) -> None:
        """Narrow the target column to the board the submitted card sits on."""
        super().__init__(*args, **kwargs)
        card_pk = self.data.get(CARD_PARAM)
        if card_pk:
            owning_board = Card.objects.filter(pk=card_pk).values("column__board_id")
            self.fields["target_column"].queryset = Column.objects.filter(
                board_id__in=owning_board
            )

    def on_valid(self, request: HttpRequest, card: DCard[Card]) -> HttpResponseRedirect:
        """Detach the card and re-insert it at the requested position."""
        target_column = self.cleaned_data["target_column"]
        target_position = self.cleaned_data["target_position"]
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
        return HttpResponseRedirect(
            with_query(
                page_reverse(BOARD_PAGE, id=target_column.board_id), moved=card.pk
            )
        )


class CreateCardForm(Form):
    """Create a card at the tail of a column subject to its WIP limit."""

    column = django_forms.ModelChoiceField(
        queryset=Column.objects.all(), widget=django_forms.HiddenInput
    )
    title = django_forms.CharField(max_length=200, widget=ComponentWidget("input"))
    body = django_forms.CharField(
        required=False, widget=ComponentWidget("textarea", rows=4)
    )

    def clean(self) -> dict[str, object]:
        """Reject creation when the column is at its WIP limit.

        The check is best-effort. The authoritative lock+check+insert
        lives in on_valid under select_for_update.
        """
        cleaned = super().clean() or {}
        column = cleaned.get("column")
        if column is not None and _is_full(column):
            msg = "Column has reached its WIP limit."
            raise django_forms.ValidationError(msg)
        return cleaned

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Append a card at the tail of the target column under a row lock."""
        column = self.cleaned_data["column"]
        with transaction.atomic():
            locked = Column.objects.select_for_update().get(pk=column.pk)
            count = locked.cards.count()
            if _is_full(locked):
                return HttpResponseBadRequest("Column has reached its WIP limit.")
            card = Card.objects.create(
                column=locked,
                title=self.cleaned_data["title"],
                body=self.cleaned_data.get("body", ""),
                position=count,
            )
        # The redirect names the new row the same way move_card names the moved
        # one, so a fetch that follows it learns the id from the final URL.
        return HttpResponseRedirect(
            with_query(page_reverse(BOARD_PAGE, id=column.board_id), created=card.pk)
        )


class CreateColumnForm(Form):
    """Append a new column to a board at the next free position."""

    # A new column has no instance to resolve, so the parent board_id is a hidden field.
    board_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    title = django_forms.CharField(max_length=120, widget=ComponentWidget("input"))
    wip_limit = django_forms.IntegerField(
        required=False, min_value=1, widget=ComponentWidget("input", type="number")
    )

    def on_valid(
        self, request: HttpRequest, board: DBoard[Board]
    ) -> HttpResponseRedirect:
        """Append a column to the board at the next free position."""
        next_position = board.columns.count()
        board.columns.create(
            title=self.cleaned_data["title"],
            position=next_position,
            wip_limit=self.cleaned_data.get("wip_limit"),
        )
        return HttpResponseRedirect(page_reverse(BOARD_SETTINGS_PAGE, id=board.pk))


class RenameBoardForm(ModelForm):
    """Rename a board, preserving its slug."""

    class Meta:
        model = Board
        fields: ClassVar = ["title"]
        instance_from_url: ClassVar = {"id": "pk"}

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Update the board title and return to settings."""
        self.save()
        return HttpResponseRedirect(
            page_reverse(BOARD_SETTINGS_PAGE, id=self.instance.pk)
        )


class ArchiveBoardForm(ModelForm):
    """Toggle the archived flag on a board."""

    class Meta:
        model = Board
        fields: ClassVar = ["archived"]
        instance_from_url: ClassVar = {"id": "pk"}

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Toggle the archived flag and redirect."""
        self.save()
        if self.instance.archived:
            return HttpResponseRedirect(page_reverse())
        return HttpResponseRedirect(
            page_reverse(BOARD_SETTINGS_PAGE, id=self.instance.pk)
        )
