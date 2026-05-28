from django import forms as django_forms
from django.db import transaction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)

from kanban.models import Board, Card, Column
from kanban.providers import DBoard
from next.forms import Form


_INPUT_CLASS = (
    "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 placeholder:text-slate-400 focus:outline-none "
    "focus:ring-2 focus:ring-slate-400 focus:border-transparent"
)
_INPUT_ATTRS = {"class": _INPUT_CLASS}
_TEXTAREA_ATTRS = {"class": _INPUT_CLASS + " resize-none", "rows": "4"}


class MoveCardForm(Form):
    """Move a card to a target column at a chosen position."""

    card_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    target_column_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    target_position = django_forms.IntegerField(
        min_value=0,
        widget=django_forms.HiddenInput,
    )

    def clean(self) -> dict[str, object]:
        """Reject moves that cross a board boundary."""
        cleaned = super().clean() or {}
        card_id = cleaned.get("card_id")
        target_column_id = cleaned.get("target_column_id")
        if card_id is None or target_column_id is None:
            return cleaned
        try:
            card = Card.objects.select_related("column__board").get(pk=card_id)
            column = Column.objects.select_related("board").get(pk=target_column_id)
        except (Card.DoesNotExist, Column.DoesNotExist) as exc:
            msg = "Unknown card or target column."
            raise django_forms.ValidationError(msg) from exc
        if card.column.board_id != column.board_id:
            msg = "Cards cannot move across boards."
            raise django_forms.ValidationError(msg)
        cleaned["_card"] = card
        cleaned["_target_column"] = column
        return cleaned

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Detach the card and re-insert it at the requested position."""
        card = self.cleaned_data["_card"]
        target_column = self.cleaned_data["_target_column"]
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
        return HttpResponseRedirect(f"/board/{target_column.board_id}/?moved={card.pk}")


class CreateCardForm(Form):
    """Create a card at the tail of a column subject to its WIP limit."""

    column_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    title = django_forms.CharField(
        max_length=200,
        widget=django_forms.TextInput(attrs=_INPUT_ATTRS),
    )
    body = django_forms.CharField(
        required=False,
        widget=django_forms.Textarea(attrs=_TEXTAREA_ATTRS),
    )

    def clean(self) -> dict[str, object]:
        """Reject creation when the column is at its WIP limit.

        The check is best-effort. The authoritative lock+check+insert
        lives in the action handler under select_for_update.
        """
        cleaned = super().clean() or {}
        column_id = cleaned.get("column_id")
        if column_id is None:
            return cleaned
        try:
            column = Column.objects.get(pk=column_id)
        except Column.DoesNotExist as exc:
            msg = "Unknown column."
            raise django_forms.ValidationError(msg) from exc
        if column.wip_limit is not None and column.cards.count() >= column.wip_limit:
            msg = "Column has reached its WIP limit."
            raise django_forms.ValidationError(msg)
        cleaned["_column"] = column
        return cleaned

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Append a card at the tail of the target column under a row lock."""
        column = self.cleaned_data["_column"]
        with transaction.atomic():
            locked = Column.objects.select_for_update().get(pk=column.pk)
            count = locked.cards.count()
            if locked.wip_limit is not None and count >= locked.wip_limit:
                return HttpResponseBadRequest("Column has reached its WIP limit.")
            Card.objects.create(
                column=locked,
                title=self.cleaned_data["title"],
                body=self.cleaned_data.get("body", ""),
                position=count,
            )
        return HttpResponseRedirect(f"/board/{column.board_id}/")


class CreateColumnForm(Form):
    """Append a new column to a board at the next free position."""

    board_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    title = django_forms.CharField(
        max_length=120,
        widget=django_forms.TextInput(attrs=_INPUT_ATTRS),
    )
    wip_limit = django_forms.IntegerField(
        required=False,
        min_value=1,
        widget=django_forms.NumberInput(attrs=_INPUT_ATTRS),
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
        return HttpResponseRedirect(f"/board/{board.pk}/settings/")


class RenameBoardForm(Form):
    """Rename a board, preserving its slug."""

    board_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    title = django_forms.CharField(
        max_length=120,
        widget=django_forms.TextInput(attrs=_INPUT_ATTRS),
    )

    def on_valid(
        self, request: HttpRequest, board: DBoard[Board]
    ) -> HttpResponseRedirect:
        """Update the board title while keeping its slug stable."""
        board.title = self.cleaned_data["title"]
        board.save(update_fields=["title"])
        return HttpResponseRedirect(f"/board/{board.pk}/settings/")


class ArchiveBoardForm(Form):
    """Toggle the `archived` flag on a board."""

    board_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    archived = django_forms.BooleanField(required=False)

    def on_valid(
        self, request: HttpRequest, board: DBoard[Board]
    ) -> HttpResponseRedirect:
        """Toggle the archived flag on the board."""
        board.archived = bool(self.cleaned_data["archived"])
        board.save(update_fields=["archived"])
        if board.archived:
            return HttpResponseRedirect("/")
        return HttpResponseRedirect(f"/board/{board.pk}/settings/")
