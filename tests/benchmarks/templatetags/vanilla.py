from django import template


register = template.Library()


@register.inclusion_tag("card.html")
def bench_card(title: str, body: str) -> dict[str, str]:
    """Render the reference card the way a plain Django project would."""
    return {"title": title, "body": body}
