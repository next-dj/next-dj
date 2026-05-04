from __future__ import annotations

from django import forms as django_forms

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
        from kanban.models import Card, Column  # noqa: PLC0415

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
        """Reject creation when the column is already at its WIP limit."""
        from kanban.models import Column  # noqa: PLC0415

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


class RenameBoardForm(Form):
    """Rename a board, preserving its slug."""

    board_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    title = django_forms.CharField(
        max_length=120,
        widget=django_forms.TextInput(attrs=_INPUT_ATTRS),
    )


class ArchiveBoardForm(Form):
    """Toggle the `archived` flag on a board."""

    board_id = django_forms.IntegerField(widget=django_forms.HiddenInput)
    archived = django_forms.BooleanField(required=False)
