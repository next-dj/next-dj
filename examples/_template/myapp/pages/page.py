from django.http import HttpRequest
from myapp.forms import ItemForm
from myapp.models import Item

from next import context


class CreateItemForm(ItemForm):
    """Bind the item form to this page, so its action name stays private to it."""


@context("items")
def items(request: HttpRequest) -> list[Item]:
    """Return the items the visitor may see, newest first."""
    return list(Item.objects.visible_to(request.user))
