import pytest

from next.boundaries import misrooted, open_elements


class TestOpenElements:
    """The elements left open at the end of a markup, with their attributes."""

    def test_the_open_elements_keep_their_attributes(self) -> None:
        markup = "<UL class='a'><li id=x><!-- <b> --><br><i></i><title><p></title>"
        assert open_elements(markup) == [("ul", " class='a'"), ("li", " id=x")]

    @pytest.mark.parametrize(
        ("markup", "names"),
        [
            ("<title-bar><p>", ["title-bar", "p"]),
            ("<script-loader x='1'><p>", ["script-loader", "p"]),
            ("<textarea-x/><p>", ["textarea-x", "p"]),
            ("<!--><p>", ["p"]),
            ("<!---><p>", ["p"]),
            ("<!-- <i> --!><p>", ["p"]),
            ("<!-- <i>", []),
            ("<div/><span>", ["div", "span"]),
            ("<svg/><p>", ["p"]),
            ("<svg><path/><g>", ["svg", "g"]),
            ("<math><mi/>", ["math"]),
            ("<svg><foreignObject><div/>", ["svg", "foreignobject", "div"]),
            ("<svg><foreignObject><svg><path/>", ["svg", "foreignobject", "svg"]),
        ],
    )
    def test_the_scan_reads_markup_as_a_browser_does(
        self, markup: str, names: list[str]
    ) -> None:
        assert [name for name, _attrs in open_elements(markup)] == names


class TestMisrooted:
    """How a body misses the one root element its boundary adopts."""

    @pytest.mark.parametrize(
        ("body", "found"),
        [
            ("<li>a</li>", None),
            ("<li>a</li><li>b</li>", "2 root elements"),
            ("", "0 root elements"),
            ("text", "loose text"),
            ("<div>a</div><style-x></style-x><p>b</p>", "3 root elements"),
            ("<title-bar>a</title-bar>", None),
            ("<!--><p>a</p>", None),
            ("<div/><p>a</p></div>", None),
            ("<svg/><p>a</p>", "2 root elements"),
            ("<svg><path/></svg>", None),
        ],
    )
    def test_misrooted_names_how_a_body_misses_one_root(
        self, body: str, found: str | None
    ) -> None:
        assert misrooted(body) == found
