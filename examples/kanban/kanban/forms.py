from typing import ClassVar

from django import forms as django_forms
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import transaction
from django.db.models import QuerySet
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)

from kanban.deps import CARD_PARAM, DCard
from kanban.models import Board, Card, Column
from kanban.services import move_card
from kanban.snapshots import board_snapshot
from next.deps import Depends, DObject
from next.forms import ComponentWidget, Form, ModelForm
from next.partial import Patches, is_partial_request
from next.urls import page_reverse, with_query


BOARD_PAGE = "board/[int:board]"
BOARD_SETTINGS_PAGE = "board/[int:board]/settings"
WIP_LIMIT_REACHED = "Column has reached its WIP limit."


def _is_full(column: Column) -> bool:
    """Return whether the column already holds as many cards as its limit allows."""
    return column.wip_limit is not None and column.cards.count() >= column.wip_limit


class MoveCardForm(Form):
    """Move a card to a target column at a chosen position.

    The target column is narrowed to the moved card's own board.
    """

    target_column = django_forms.ModelChoiceField(
        queryset=Column.objects.none(), widget=django_forms.HiddenInput
    )
    target_position = django_forms.IntegerField(
        min_value=0, widget=django_forms.HiddenInput
    )

    class Meta:
        transport = "any"

    def __init__(
        self, *args, targets: QuerySet[Column] | None = None, **kwargs
    ) -> None:
        """Offer `targets` as the columns the card may land in, none without them."""
        super().__init__(*args, **kwargs)
        if targets is not None:
            self.fields["target_column"].queryset = targets

    @classmethod
    def get_form_kwargs(
        cls,
        request: HttpRequest,
        user: AbstractBaseUser | AnonymousUser = Depends("scope"),
    ) -> dict[str, object]:
        """Hand the constructor the columns of the visible board the card sits on."""
        card_pk = request.POST.get(CARD_PARAM, "")
        visible = Board.objects.visible_to(user)
        is_pk = card_pk.isascii() and card_pk.isdigit()
        boards = visible.filter(columns__cards=card_pk) if is_pk else visible.none()
        targets = Column.objects.filter(board__in=boards)
        return {**super().get_form_kwargs(request), "targets": targets}

    def on_valid(self, request: HttpRequest, card: DCard[Card]) -> HttpResponse:
        """Move the card, answering the island with the board it now reads."""
        target_column = self.cleaned_data["target_column"]
        move_card(card, target_column, self.cleaned_data["target_position"])
        if is_partial_request(request):
            snapshot = board_snapshot(target_column.board, request)
            return Patches(request).context(board=snapshot).response()
        return HttpResponseRedirect(
            with_query(
                page_reverse(BOARD_PAGE, board=target_column.board_id), moved=card.pk
            )
        )


class CreateCardForm(Form):
    """Create a card at the tail of a column subject to its WIP limit."""

    column = django_forms.ModelChoiceField(
        queryset=Column.objects.none(), widget=django_forms.HiddenInput
    )
    title = django_forms.CharField(max_length=200, widget=ComponentWidget("input"))
    body = django_forms.CharField(
        required=False, widget=ComponentWidget("textarea", rows=4)
    )

    def __init__(
        self, *args, columns: QuerySet[Column] | None = None, **kwargs
    ) -> None:
        """Offer `columns` as the columns a card may land in, none without them."""
        super().__init__(*args, **kwargs)
        if columns is not None:
            self.fields["column"].queryset = columns

    @classmethod
    def get_form_kwargs(
        cls,
        request: HttpRequest,
        user: AbstractBaseUser | AnonymousUser = Depends("scope"),
    ) -> dict[str, object]:
        """Hand the constructor the columns of every board the visitor may see."""
        columns = Column.objects.filter(board__in=Board.objects.visible_to(user))
        return {**super().get_form_kwargs(request), "columns": columns}

    def clean(self) -> dict[str, object]:
        """Reject creation when the column is at its WIP limit.

        Best effort, the authoritative check runs in `on_valid` under a row lock.
        """
        cleaned = super().clean() or {}
        column = cleaned.get("column")
        if column is not None and _is_full(column):
            raise django_forms.ValidationError(WIP_LIMIT_REACHED)
        return cleaned

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Append a card at the tail of the target column under a row lock."""
        column = self.cleaned_data["column"]
        with transaction.atomic():
            locked = Column.objects.select_for_update().get(pk=column.pk)
            count = locked.cards.count()
            if _is_full(locked):
                return HttpResponseBadRequest(
                    WIP_LIMIT_REACHED, content_type="text/plain; charset=utf-8"
                )
            card = Card.objects.create(
                column=locked,
                title=self.cleaned_data["title"],
                body=self.cleaned_data.get("body", ""),
                position=count,
            )
        # The redirect names the new row as move_card does, so a fetch learns the id.
        return HttpResponseRedirect(
            with_query(page_reverse(BOARD_PAGE, board=column.board_id), created=card.pk)
        )


class CreateColumnForm(Form):
    """Append a new column to the board whose settings page posted the form."""

    title = django_forms.CharField(max_length=120, widget=ComponentWidget("input"))
    wip_limit = django_forms.IntegerField(
        required=False, min_value=1, widget=ComponentWidget("input", type="number")
    )

    def on_valid(
        self, request: HttpRequest, board: DObject[Board]
    ) -> HttpResponseRedirect:
        """Append a column to the board at the next free position."""
        next_position = board.columns.count()
        board.columns.create(
            title=self.cleaned_data["title"],
            position=next_position,
            wip_limit=self.cleaned_data.get("wip_limit"),
        )
        return HttpResponseRedirect(page_reverse(BOARD_SETTINGS_PAGE, board=board.pk))


class BoardSettingsForm(ModelForm):
    """Base of the board settings forms, abstract so it registers no action itself."""

    class Meta:
        abstract = True

    @classmethod
    def get_initial(cls, board: DObject[Board]) -> Board:
        """Bind the board the settings page names, loaded through `visible_to`."""
        return board


class RenameBoardForm(BoardSettingsForm):
    """Rename a board, preserving its slug."""

    class Meta:
        model = Board
        fields: ClassVar = ["title"]

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Update the board title and return to settings."""
        self.save()
        return HttpResponseRedirect(
            page_reverse(BOARD_SETTINGS_PAGE, board=self.instance.pk)
        )


class ArchiveBoardForm(BoardSettingsForm):
    """Toggle the archived flag on a board."""

    class Meta:
        model = Board
        fields: ClassVar = ["archived"]

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Toggle the archived flag and redirect."""
        self.save()
        if self.instance.archived:
            return HttpResponseRedirect(page_reverse())
        return HttpResponseRedirect(
            page_reverse(BOARD_SETTINGS_PAGE, board=self.instance.pk)
        )


class SignInForm(Form):
    """Sign a demo member in, so the board can name who is watching it."""

    username = django_forms.CharField(max_length=150, widget=ComponentWidget("input"))
    password = django_forms.CharField(widget=ComponentWidget("input", type="password"))

    def clean(self) -> dict[str, object]:
        """Reject a username and password pair that names no active account."""
        cleaned = super().clean() or {}
        self.user = authenticate(
            username=cleaned.get("username"), password=cleaned.get("password")
        )
        if self.user is None:
            msg = "Unknown username or password."
            raise django_forms.ValidationError(msg)
        return cleaned

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Start the session and go back to the board list."""
        login(request, self.user)
        return HttpResponseRedirect(page_reverse())


class SignOutForm(Form):
    """End the session of the signed-in member."""

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Log out and go back to the board list."""
        logout(request)
        return HttpResponseRedirect(page_reverse())
