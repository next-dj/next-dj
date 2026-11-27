from types import MappingProxyType


HOSTILE = MappingProxyType(
    {
        "open-quote": '<nav><a x="',
        "open-comment": "<nav><!--",
        "unclosed-script": "<nav><script></scriptx",
        "unclosed-container": '<nav><a href="/docs/intro">',
        "bare-lt": "<nav><",
        "open-attributes": "<nav><a b ",
        "quoted-opener": "<div title='<nav>'>",
        "quoted-gt": "</div x='>'><nav>",
        "attribute-container": "<nav><div data-next-nav>",
        "repeated-marks": '<nav><a aria-current=x aria-current="y" href=/docs>',
    }
)
"""Markup units a backtracking active-link walk would turn super-linear when repeated."""


def flood(unit: str, size: int) -> str:
    """Repeat `unit` into a document of about `size` characters."""
    return unit * (size // len(unit))


PARSER_VOID = (
    "area",
    "base",
    "basefont",
    "bgsound",
    "br",
    "col",
    "embed",
    "frame",
    "hr",
    "img",
    "input",
    "keygen",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
)
"""The start tags the HTML parsing algorithm never leaves open, spelled out by hand."""
