"""The active-link pass one rendered fragment runs, bound per request by the router."""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Final, Protocol, override

from django.template import Context, Template
from django.template.base import Node, NodeList
from django.utils.safestring import SafeString


class FixedText(Protocol):
    """The text of a container no request changes, its anchors read once for all."""

    @property
    def html(self) -> str:
        """Return the text as the template source spells it."""

    def mark(
        self, path: Callable[[], str], origin: Callable[[], str] | None = None
    ) -> str:
        """Return the text marked against the path with the query `path` reads."""


class FragmentPass(Protocol):
    """The active-link pass a page binds for the fragments it renders."""

    def __call__(self, html: str) -> str:
        """Return `html` with its current links marked."""

    def compiled(self, template: Template) -> bool:
        """Whether every container `template` writes marks its own links."""

    def fixed(self, text: FixedText) -> str:
        """Return the text of a fixed container with its current links marked."""


fragment_marks: Final[ContextVar[FragmentPass | None]] = ContextVar(
    "next_fragment_marks", default=None
)
"""The pass that marks the current links of one fragment, `None` while none is bound."""


_inside: ContextVar[bool] = ContextVar("next_fragment_inside", default=False)


def marked(render: Callable[[], str]) -> str:
    """Return what `render` writes, its links marked unless an outer fragment does.

    The outermost fragment alone runs the pass, so a nested one keeps its frame modes.
    """
    marks = fragment_marks.get()
    if marks is None or _inside.get():
        return render()
    token = _inside.set(True)
    try:
        html = render()
    finally:
        _inside.reset(token)
    return marks(html)


def rendered(template: Template, render: Callable[[], str]) -> str:
    """Return what `render` writes of `template`, marked unless it marks its own.

    A template whose source shows every container wraps each one, so no pass runs.
    """
    marks = fragment_marks.get()
    if marks is None or _inside.get() or marks.compiled(template):
        return render()
    return marked(render)


class ContainerNode(Node):
    """The nodes of one navigation container, its links marked in its output alone."""

    child_nodelists = ("nodelist",)

    def __init__(self, nodelist: NodeList, fixed: FixedText | None = None) -> None:
        """Hold the nodes from the opening tag to the closing one, read when all text.

        A container of text alone is read at compile time, so a request only compares.
        """
        self.nodelist = nodelist
        self.fixed = fixed

    @override
    def render(self, context: Context) -> SafeString:
        """Render the container and mark the links it holds for the bound request."""
        fixed = self.fixed
        if fixed is None:
            return SafeString(marked(lambda: self.nodelist.render(context)))
        marks = fragment_marks.get()
        if marks is None or _inside.get():
            return SafeString(fixed.html)
        return SafeString(marks.fixed(fixed))


__all__ = [
    "ContainerNode",
    "FixedText",
    "FragmentPass",
    "fragment_marks",
    "marked",
    "rendered",
]
