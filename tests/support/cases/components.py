from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ModeCase:
    """Where a component call declares its mode and what one render of it leaves."""

    id: str
    loading: str | None
    declared: str | None
    tag: str
    jobs: int | None
    state: str | None
    inline: bool


MODE_CASES: tuple[ModeCase, ...] = (
    ModeCase("plain", None, None, "", None, None, inline=True),
    ModeCase("loading-file", "L", None, "", 1, "loading", inline=False),
    ModeCase("call-inline", "L", None, " defer='inline'", 0, "ready", inline=True),
    ModeCase("call-stream", None, None, " defer='stream'", 1, "loading", inline=False),
    ModeCase("module-inline", "L", "inline", "", 0, "ready", inline=True),
    ModeCase("client-trip", None, "visible", "", 1, "loading", inline=False),
    ModeCase("wins", None, "stream", " defer='inline'", 0, "ready", inline=True),
)


@dataclass(frozen=True, slots=True)
class PropRenderCase:
    """One component call, the calling context and the text its render must carry."""

    id: str
    call_site: str
    context: dict[str, object] = field(default_factory=dict)
    present: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()


SAFE_LITERAL_CASES: tuple[PropRenderCase, ...] = (
    # Django marks a quoted literal safe, so `<slug>` would render as a tag.
    PropRenderCase(
        "literal-escapes",
        '{% component "card" body="visit /s/<slug>/ now" %}',
        present=("/s/&lt;slug&gt;/",),
        absent=("<slug>",),
    ),
    PropRenderCase(
        "literal-safe-opt-in",
        '{% component "card" body="<em>raw</em>"|safe %}',
        present=("<em>raw</em>",),
        absent=("&lt;em&gt;",),
    ),
    PropRenderCase(
        "variable-stays-safe",
        '{% component "card" body=html_blob %}',
        {"html_blob": "<b>safe</b>"},
        present=("<b>safe</b>",),
        absent=("&lt;b&gt;",),
    ),
)


@dataclass(frozen=True, slots=True)
class InterpolationCase:
    """A string prop with `{{ expr }}` inside, the body reading it and its render."""

    id: str
    call_site: str
    body: str
    context: dict[str, object]
    expected: str


INTERPOLATION_CASES: tuple[InterpolationCase, ...] = (
    InterpolationCase(
        "text-around", 'title="Rows ({{ n }})"', "{{ title }}", {"n": 3}, "Rows (3)"
    ),
    InterpolationCase(
        "two-expressions",
        'title="{{ a }}-{{ b }}"',
        "{{ title }}",
        {"a": 1, "b": 2},
        "1-2",
    ),
    InterpolationCase(
        "filter-inside",
        'title="{{ name|upper }}!"',
        "{{ title }}",
        {"name": "ada"},
        "ADA!",
    ),
    InterpolationCase(
        "whitespace-kept", 'title="  {{  n  }}  "', "{{ title }}", {"n": 3}, "  3  "
    ),
    InterpolationCase(
        "lone-expression-keeps-type",
        'title="{{ n }}"',
        "{{ title|add:1 }}",
        {"n": 5},
        "6",
    ),
    InterpolationCase(
        "lone-expression-keeps-list",
        'title="{{ items }}"',
        "{{ title|length }}",
        {"items": [1, 2, 3]},
        "3",
    ),
    InterpolationCase(
        "missing-variable-is-empty", 'title="x {{ gone }} y"', "{{ title }}", {}, "x  y"
    ),
    InterpolationCase(
        "lone-value-escapes-once",
        'title="{{ raw }}"',
        "{{ title }}",
        {"raw": "<b>"},
        "&lt;b&gt;",
    ),
    InterpolationCase(
        "mixed-value-escapes-once",
        'title="a {{ raw }}"',
        "{{ title }}",
        {"raw": "<b>"},
        "a &lt;b&gt;",
    ),
    InterpolationCase(
        "filter-on-the-literal-keeps-django-meaning",
        'title="{{ n }}"|upper',
        "{{ title }}",
        {"n": 3},
        "{{ N }}",
    ),
    InterpolationCase(
        "plain-literal-untouched", 'title="no braces"', "{{ title }}", {}, "no braces"
    ),
)
