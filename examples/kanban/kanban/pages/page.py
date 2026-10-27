from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import HttpRequest
from kanban.models import Board
from kanban.realtime import public_profile

from next import Depends, context


@context("boards")
def boards(user: AbstractBaseUser | AnonymousUser = Depends("scope")) -> list[Board]:
    """Return active boards in reverse-chronological order for the index page."""
    return list(Board.objects.visible_to(user).active())


@context("active_boards_count", inherit_context=True)
def active_boards_count(
    user: AbstractBaseUser | AnonymousUser = Depends("scope"),
) -> int:
    """Return the count of active boards exposed to every descendant page."""
    return Board.objects.visible_to(user).active().count()


@context("archived_count")
def archived_count(user: AbstractBaseUser | AnonymousUser = Depends("scope")) -> int:
    """Return the number of archived boards for the empty-state copy."""
    return Board.objects.visible_to(user).archived().count()


@context("viewer", inherit_context=True)
def viewer(request: HttpRequest) -> dict[str, str] | None:
    """Return the signed-in member as the header shows them, None for a guest."""
    if not request.user.is_authenticated:
        return None
    return public_profile(request.user)
