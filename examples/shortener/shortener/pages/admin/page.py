from django import forms
from django.http import Http404, HttpRequest, HttpResponse
from shortener.cache import pending_clicks
from shortener.models import Link
from shortener.services import delete_link, find_link, top_links

from next import action, context, page
from next.forms import ModelForm
from next.pages import MetadataDict
from next.partial import Patches
from next.urls import page_reverse_lazy


metadata: MetadataDict = {"title": "Admin panel", "robots": {"index": False}}

page.public()


@context("recent_links", inherit_context=True)
def recent_links() -> list[Link]:
    return top_links(10)


@context("pending_clicks")
def admin_pending_clicks() -> dict[str, int]:
    return pending_clicks()


class EditLinkForm(ModelForm):
    url = forms.URLField(max_length=2000, assume_scheme="https")

    class Meta:
        model = Link
        fields = ("url",)
        success_url = page_reverse_lazy("admin")
        success_message = "Destination updated."

    @classmethod
    def get_initial(cls, request: HttpRequest) -> Link | None:
        """Resolve the edited link from the posted slug, None on a plain render."""
        slug = request.POST.get("slug")
        if not slug:
            return None
        return find_link(slug)


@action("delete_link")
def delete_link_row(request: HttpRequest) -> HttpResponse:
    """Delete the posted link and drop its row from the list in place.

    A live runtime removes the addressed row by its slug key without a reload.
    """
    slug = request.POST.get("slug", "")
    if not delete_link(slug=slug):
        raise Http404
    return (
        Patches(request)
        .remove({"css": f'li[data-next-key="{slug}"]'})
        .response(fallback="/admin/")
    )
