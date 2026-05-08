from django.http import HttpResponseRedirect


def render() -> HttpResponseRedirect:
    """Redirect the bare site root to the polls index.

    The polls list lives under /polls/ so URL nesting stays consistent
    with the detail page at /polls/<id>/. Sending the root straight
    there spares first-time visitors an empty landing.
    """
    return HttpResponseRedirect("/polls/")
