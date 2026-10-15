from django.http import HttpResponseRedirect

from next.urls import page_reverse


def render() -> HttpResponseRedirect:
    """Redirect the bare site root to the polls index.

    Keeps URL nesting consistent with the /polls/<id>/ detail page, not an empty root.
    """
    return HttpResponseRedirect(page_reverse("polls"))
