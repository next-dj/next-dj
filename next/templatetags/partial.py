"""Template tag library exposing `{% zone %}`, `{% more %}` and its page filters.

The tags live in `next.partial`, gathered here for the builtin wiring.
"""

from django.template import Library

from next.partial.more import do_more, next_page, previous_page
from next.partial.zone import do_zone


register = Library()
register.tag("zone", do_zone)
register.tag("more", do_more)
register.filter("next_page", next_page)
register.filter("previous_page", previous_page)


__all__ = ["register"]
