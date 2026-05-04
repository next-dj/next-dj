from kanban.models import Board

from next.pages import context


@context("boards")
def boards() -> list[Board]:
    """Return active boards in reverse-chronological order for the index page."""
    return list(Board.objects.filter(archived=False).order_by("-created_at"))


@context("active_boards_count", inherit_context=True)
def active_boards_count() -> int:
    """Return the count of active boards exposed to every descendant page."""
    return Board.objects.filter(archived=False).count()


@context("archived_count")
def archived_count() -> int:
    """Return the number of archived boards for the empty-state copy."""
    return Board.objects.filter(archived=True).count()
