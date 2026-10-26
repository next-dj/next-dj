from django.http import HttpRequest
from kanban.models import Board
from kanban.realtime import board_topic, can_watch_board

from next.deps import DObject
from next.realtime import TopicStream


def render(request: HttpRequest, board: DObject[Board]) -> TopicStream:
    """Open the board topic with its watchers listed by name and initials."""
    return TopicStream(
        request,
        board_topic(board.pk),
        authorize=can_watch_board,
        presence="kanban.realtime.identify_member",
    )
