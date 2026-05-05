from django.http import HttpResponseRedirect
from kanban.forms import ArchiveBoardForm, CreateColumnForm, RenameBoardForm
from kanban.models import Board
from kanban.providers import DBoard

from next.forms import action
from next.pages import context


@context("settings_active")
def settings_active() -> bool:
    """Mark the settings tab as active for the layout toolbar."""
    return True


@action("create_column", namespace="kanban", form_class=CreateColumnForm)
def create_column(
    form: CreateColumnForm,
    board: DBoard[Board],
) -> HttpResponseRedirect:
    """Append a column to the board at the next free position."""
    next_position = board.columns.count()
    board.columns.create(
        title=form.cleaned_data["title"],
        position=next_position,
        wip_limit=form.cleaned_data.get("wip_limit"),
    )
    return HttpResponseRedirect(f"/board/{board.pk}/settings/")


@action("rename_board", namespace="kanban", form_class=RenameBoardForm)
def rename_board(
    form: RenameBoardForm,
    board: DBoard[Board],
) -> HttpResponseRedirect:
    """Update the board title while keeping its slug stable."""
    board.title = form.cleaned_data["title"]
    board.save(update_fields=["title"])
    return HttpResponseRedirect(f"/board/{board.pk}/settings/")


@action("archive_board", namespace="kanban", form_class=ArchiveBoardForm)
def archive_board(
    form: ArchiveBoardForm,
    board: DBoard[Board],
) -> HttpResponseRedirect:
    """Toggle the archived flag on the board."""
    board.archived = bool(form.cleaned_data["archived"])
    board.save(update_fields=["archived"])
    if board.archived:
        return HttpResponseRedirect("/")
    return HttpResponseRedirect(f"/board/{board.pk}/settings/")
