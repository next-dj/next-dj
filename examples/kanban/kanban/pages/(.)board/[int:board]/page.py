from kanban.models import Board, Column

from next import context, page
from next.deps import DObject
from next.pages import MetadataDict


@context("board")
def board(board: DObject[Board]) -> Board:
    """Return the board the summary covers."""
    return board


@context("lanes")
def lanes(board: DObject[Board]) -> list[Column]:
    """Return the columns of the board with their cards, for the summary."""
    return list(board.columns.prefetch_related("cards"))


@page.metadata
def board_peek_meta(board: DObject[Board]) -> MetadataDict:
    """Title the tab after the board while the summary covers the index."""
    return {"title": f"Peek · {board.title}"}
