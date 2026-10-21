from typing import Final


PERM: Final = "auth.view_user"
SECRET_ZONE: Final = '{% zone "cz" %}SECRET{% endzone %}'

GUARDED_SET_SLOT: Final = (
    f'<div>{{% has_perm "{PERM}" %}}{{% set_slot "content" %}}{{% endhas_perm %}}'
    '{% set_slot "aside" %}</div>'
)
GUARDED_SLOT_VARIABLE: Final = (
    f'<div>{{% has_perm "{PERM}" %}}{{{{ slot_content }}}}{{% endhas_perm %}}</div>'
)
GUARDED_CHILDREN: Final = (
    f'<div>{{% has_perm "{PERM}" %}}{{{{ children }}}}{{% endhas_perm %}}</div>'
)
PLAIN_COMPONENT: Final = '<p>{% set_slot "aside" %}{{ children }}</p>'


def wrap(component: str, inner: str = SECRET_ZONE) -> str:
    """Return a page body that hands `inner` to `component` as its children."""
    return f'{{% #component "{component}" %}}{inner}{{% /component %}}'


def fill_slot(slot: str, inner: str = SECRET_ZONE, component: str = "guarded") -> str:
    """Return a page body that fills `slot` of `component` with `inner`."""
    return wrap(component, f'{{% #slot "{slot}" %}}{inner}{{% /slot %}}')


CHILDREN_BODY: Final = wrap("guarded")
NESTED_COMPONENT_BODY: Final = fill_slot(
    "content", fill_slot("aside", component="plain")
)
