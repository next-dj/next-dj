from django.db import transaction
from django.db.models import Prefetch, QuerySet
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.middleware.csrf import get_token
from kanban.forms import CreateCardForm, MoveCardForm
from kanban.models import Board, Card, Column
from kanban.providers import DBoard

from next.forms import action, form_action_manager
from next.pages import context


@context("board_object", inherit_context=True)
def board_object(active_board: DBoard[Board]) -> Board:
    """Expose the board ORM instance for server-rendered iteration."""
    return active_board


@context("board_url_kwargs", inherit_context=True)
def board_url_kwargs(active_board: DBoard[Board]) -> dict[str, int]:
    """Expose the reverse() kwargs for the current board route.

    Shared by the nav_link calls in the board layout so the tabs link
    back to themselves using `url_name` without hard-coding the URL
    converter on the call site.
    """
    return {"id": active_board.pk}


@context("columns", inherit_context=True)
def columns(active_board: DBoard[Board]) -> QuerySet[Column]:
    """Return the ordered columns of the active board for the template."""
    return active_board.columns.all()


@context("moved_card", inherit_context=True)
def moved_card(request: HttpRequest) -> Card | None:
    """Return the just-moved card when the redirect carried a `?moved=` flag."""
    pk = request.GET.get("moved")
    if not pk:
        return None
    try:
        return Card.objects.select_related("column").get(pk=pk)
    except Card.DoesNotExist:
        return None


@context("board", inherit_context=True, serialize=True)
def board_payload(
    active_board: DBoard[Board],
    request: HttpRequest,
) -> dict[str, object]:
    """Expose the full board tree under the merged board JS context key.

    Drives the React layer at `window.Next.context.board` and includes
    excerpts and wip_limits so client renders match the SSR fallback.
    """
    cols = active_board.columns.prefetch_related(
        Prefetch("cards", queryset=Card.objects.order_by("position"))
    ).order_by("position")
    return {
        "id": active_board.pk,
        "title": active_board.title,
        "slug": active_board.slug,
        "archived": active_board.archived,
        "csrf": get_token(request),
        "move_card_url": form_action_manager.get_action_url("kanban:move_card"),
        "columns": [
            {
                "id": col.id,
                "title": col.title,
                "position": col.position,
                "wip_limit": col.wip_limit,
                "cards": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "position": c.position,
                        "excerpt": c.excerpt,
                    }
                    for c in col.cards.all()
                ],
            }
            for col in cols
        ],
    }


@action("move_card", namespace="kanban", form_class=MoveCardForm)
def move_card(form: MoveCardForm) -> HttpResponseRedirect:
    """Detach the card and re-insert it at the requested position."""
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


@action("create_card", namespace="kanban", form_class=CreateCardForm)
def create_card(form: CreateCardForm) -> HttpResponse:
    """Append a card at the tail of the target column under a row lock.

    The form clean is best-effort. The authoritative WIP-limit check
    runs here under select_for_update so concurrent posts cannot both
    pass the limit.
    """
    column = form.cleaned_data["_column"]
    with transaction.atomic():
        locked = Column.objects.select_for_update().get(pk=column.pk)
        count = locked.cards.count()
        if locked.wip_limit is not None and count >= locked.wip_limit:
            return HttpResponseBadRequest("Column has reached its WIP limit.")
        Card.objects.create(
            column=locked,
            title=form.cleaned_data["title"],
            body=form.cleaned_data.get("body", ""),
            position=count,
        )
    return HttpResponseRedirect(f"/board/{column.board_id}/")
