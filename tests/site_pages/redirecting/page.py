from django.http import HttpResponseRedirect


def render() -> HttpResponseRedirect:
    """Short-circuit the page with an early redirect before any zone render."""
    return HttpResponseRedirect("/login/")
