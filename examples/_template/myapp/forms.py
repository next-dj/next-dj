from typing import ClassVar

from django.http import HttpRequest, HttpResponseRedirect

from myapp.models import Item
from myapp.services import create_item
from next.forms import ModelForm, redirect_to_origin


class ItemForm(ModelForm):
    """Create an item through `create_item`, abstract so each page binds its own."""

    class Meta:
        abstract = True
        model = Item
        fields: ClassVar = ["title"]
        permission_required = "myapp.add_item"

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Save through the service, so the site and the admin write the same way."""
        create_item(request.user, title=self.cleaned_data["title"])
        return redirect_to_origin(request)
