def render() -> str:
    """Return a dynamic body so a zone request to this page is rejected."""
    return '{% zone "ghost" %}<p>ghost</p>{% endzone %}'
