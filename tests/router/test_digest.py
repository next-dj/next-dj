import re

import pytest

from next.pages.layouts import META, SLOT, ZONE, closing, opening, sealed
from next.router.digest import (
    REGION_STANDIN,
    SLOT_STANDIN,
    body_bounds,
    chrome,
    chrome_digest,
    chrome_zones,
    find_slot,
    fold,
    normalise,
    regions,
    split_head,
    zone_html,
    zone_name,
)
from next.static.runtime import ASSETS_MARK, RouterRender


SEAL = "5ea1ed00"
OTHER = "0badc0de"
IDENT = "0123456789abcdef"


def _chrome_digest(text: str, seal: str = SEAL) -> str:
    return chrome_digest(normalise(text, seal))


class TestRegions:
    """`regions` pairs the sealed comments of one kind, outermost first."""

    def test_a_region_spans_both_comments(self) -> None:
        html = f"a{sealed(ZONE, SEAL, 'z')}b"
        ((start, end),) = regions(html, ZONE, SEAL)
        assert html[start:end] == sealed(ZONE, SEAL, "z")

    def test_a_nested_region_belongs_to_its_parent(self) -> None:
        inner = sealed(ZONE, SEAL, "i")
        html = sealed(ZONE, SEAL, f"<p>{inner}</p>") + sealed(ZONE, SEAL, "x")
        found = regions(html, ZONE, SEAL)
        assert len(found) == 2
        assert html[found[0][0] : found[0][1]].count(opening(ZONE, SEAL)) == 2

    def test_another_seal_and_a_stray_closing_are_ignored(self) -> None:
        html = closing(ZONE, SEAL) + sealed(ZONE, OTHER, "forged")
        assert regions(html, ZONE, SEAL) == []

    def test_an_unclosed_region_is_ignored(self) -> None:
        assert regions(opening(ZONE, SEAL) + "open", ZONE, SEAL) == []


class TestNormalise:
    """Per-response tokens and re-sent regions never move a digest."""

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            pytest.param(
                '<script nonce="aaa">x</script>',
                '<script nonce="bbb">x</script>',
                id="nonce",
            ),
            pytest.param(
                '<input type="hidden" name="csrfmiddlewaretoken" value="one">',
                '<input type="hidden" name="csrfmiddlewaretoken" value="two">',
                id="csrf",
            ),
            pytest.param(
                sealed(ZONE, SEAL, "<p>one</p>"),
                sealed(ZONE, SEAL, "<p>two</p>"),
                id="zone-body",
            ),
            pytest.param(
                sealed(META, SEAL, "<title>A</title>"),
                sealed(META, SEAL, "<title>B</title>"),
                id="metadata",
            ),
            pytest.param(
                sealed(ASSETS_MARK, SEAL, '<link href="/a.css">'),
                sealed(ASSETS_MARK, SEAL, '<link href="/b.css">'),
                id="injected-assets",
            ),
        ],
    )
    def test_a_per_response_difference_keeps_the_digest(
        self, first: str, second: str
    ) -> None:
        assert _chrome_digest(f"<nav>{first}</nav>") == _chrome_digest(
            f"<nav>{second}</nav>"
        )

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            pytest.param("<h1>Post one</h1>", "<h1>Post two</h1>", id="article-title"),
            pytest.param(
                "<footer>/blog/</footer>",
                "<footer>/about/</footer>",
                id="path-from-a-context-processor",
            ),
            pytest.param("<b>3 polls</b>", "<b>4 polls</b>", id="descendant-count"),
            pytest.param(
                '<input name="next" value="/admin/a/">',
                '<input name="next" value="/admin/b/">',
                id="request-path-in-a-form",
            ),
            pytest.param(
                '<a href="?window=1h">1h</a>',
                '<a href="?window=1d">1d</a>',
                id="query-outside-a-zone",
            ),
            pytest.param(
                '<a aria-current="page">Home</a>',
                "<a>Home</a>",
                id="active-state-rendered-before-the-pass",
            ),
            pytest.param(
                sealed(ZONE, OTHER, "<p>one</p>"),
                sealed(ZONE, OTHER, "<p>two</p>"),
                id="zone-sealed-by-content",
            ),
            pytest.param(
                '<div data-next-token="t1"></div>',
                '<div data-next-token="t2"></div>',
                id="client-trip-token",
            ),
            pytest.param(
                '<div id="b-0a0a0a0a-1"></div>',
                '<div id="b-1b1b1b1b-1"></div>',
                id="boundary-id",
            ),
        ],
    )
    def test_a_chrome_difference_moves_the_digest(
        self, first: str, second: str
    ) -> None:
        assert _chrome_digest(f"<nav>{first}</nav>") != _chrome_digest(
            f"<nav>{second}</nav>"
        )

    def test_a_zone_leaves_a_stand_in(self) -> None:
        text = normalise(f"a{sealed(ZONE, SEAL, 'z')}b", SEAL)
        assert text == f"a{REGION_STANDIN}b"

    def test_overlapping_regions_count_once(self) -> None:
        meta = sealed(META, SEAL, "<title>t</title>")
        text = normalise(sealed(ZONE, SEAL, meta), SEAL)
        assert text == REGION_STANDIN

    def test_a_zone_around_the_slot_counts_as_chrome(self) -> None:
        flash = sealed(ZONE, SEAL, "<p>flash</p>")
        wrapper = f"<main data-next-zone='main'><h2>T</h2>{flash}{SLOT_STANDIN}</main>"
        text = normalise(f"<body>{sealed(ZONE, SEAL, wrapper)}</body>", SEAL)
        assert text == (
            f"<body><main data-next-zone='main'><h2>T</h2>{REGION_STANDIN}"
            f"{SLOT_STANDIN}</main></body>"
        )

    def test_the_chrome_a_zone_around_the_slot_wraps_moves_the_digest(self) -> None:
        def chrome_of(title: str) -> str:
            return sealed(ZONE, SEAL, f"<main><h2>{title}</h2>{SLOT_STANDIN}</main>")

        assert _chrome_digest(chrome_of("one")) != _chrome_digest(chrome_of("two"))

    def test_every_csrf_input_is_masked(self) -> None:
        text = (
            '<input name="csrfmiddlewaretoken" value="one"><b value="kept">'
            "<input name='csrfmiddlewaretoken' value='two'>"
        )
        assert normalise(text, SEAL) == (
            '<input name="csrfmiddlewaretoken" value=""><b value="kept">'
            "<input name='csrfmiddlewaretoken' value=\"\">"
        )

    def test_an_unclosed_csrf_tag_returns_the_text(self) -> None:
        text = 'name="csrfmiddlewaretoken" ' * 4000
        assert normalise(text, SEAL) == text


class TestDigest:
    """`chrome_digest` is 16 hex characters, stable for one input."""

    def test_the_digest_is_short_hex(self) -> None:
        digest = chrome_digest("<nav>x</nav>")
        assert len(digest) == 16
        assert int(digest, 16) >= 0
        assert digest == chrome_digest("<nav>x</nav>")


class TestSlots:
    """`find_slot` finds a slot by its id and seal inside one region."""

    def test_the_inner_and_outer_bounds(self) -> None:
        html = f"<main>{sealed(SLOT, SEAL, '<p>x</p>', IDENT)}</main>"
        inner, outer = find_slot(html, IDENT, SEAL, (0, len(html))) or ((0, 0), (0, 0))
        assert html[inner[0] : inner[1]] == "<p>x</p>"
        assert html[outer[0] : outer[1]] == sealed(SLOT, SEAL, "<p>x</p>", IDENT)

    @pytest.mark.parametrize(
        "html",
        [
            pytest.param(sealed(SLOT, OTHER, "x", IDENT), id="look-alike-seal"),
            pytest.param(opening(SLOT, SEAL, IDENT) + "x", id="unclosed"),
            pytest.param("<p>no slot</p>", id="absent"),
        ],
    )
    def test_no_slot_without_both_sealed_comments(self, html: str) -> None:
        assert find_slot(html, IDENT, SEAL, (0, len(html))) is None

    def test_the_slot_leaves_a_stand_in_in_its_chrome(self) -> None:
        html = f"<a>{sealed(SLOT, SEAL, 'x', IDENT)}<b>"
        _inner, outer = find_slot(html, IDENT, SEAL, (0, len(html))) or ((0, 0), (0, 0))
        assert chrome(html, (0, len(html)), outer) == f"<a>{SLOT_STANDIN}<b>"

    def test_the_position_of_the_slot_counts(self) -> None:
        before = f"AB{SLOT_STANDIN}"
        after = f"A{SLOT_STANDIN}B"
        assert chrome_digest(before) != chrome_digest(after)


class TestHeadAndBody:
    """The head part ends after the `<body>` tag and the body bounds hold its children."""

    def test_split_head(self) -> None:
        assert split_head('<html><head></head><body class="a"><p>') == (
            '<html><head></head><body class="a">',
            "<p>",
        )
        assert split_head("<main></main>") is None
        assert split_head("<bodyx>") is None
        assert split_head("<body") is None

    def test_body_bounds(self) -> None:
        html = '<html><body class="x"><p>in</p></body></html>'
        start, end = body_bounds(html) or (0, 0)
        assert html[start:end] == "<p>in</p>"

    def test_body_bounds_without_a_close_run_to_the_end(self) -> None:
        html = "<body><p>in"
        start, end = body_bounds(html) or (0, 0)
        assert html[start:end] == "<p>in"

    def test_no_body_bounds(self) -> None:
        assert body_bounds("<main></main>") is None
        assert body_bounds("<body") is None


class TestZones:
    """A sealed zone region is named by its wrapper and morphed without its comments."""

    def test_the_name_and_the_html(self) -> None:
        wrapper = "<div data-next-zone='flash'><p>x</p></div>"
        html = f"<header>{sealed(ZONE, SEAL, wrapper)}</header>"
        ((name, bounds),) = chrome_zones(html, SEAL, None)
        assert name == "flash"
        assert zone_html(html, bounds, SEAL) == wrapper

    def test_a_zone_inside_the_slot_is_left_out(self) -> None:
        zone = sealed(ZONE, SEAL, '<div data-next-zone="in"></div>')
        html = f"<a>{zone}</a>"
        assert list(chrome_zones(html, SEAL, (0, len(html)))) == []

    def test_a_zone_around_the_slot_yields_the_zones_inside_it(self) -> None:
        flash = sealed(ZONE, SEAL, '<p data-next-zone="flash"></p>')
        slot = sealed(ZONE, SEAL, '<p data-next-zone="in"></p>')
        before = f'<main data-next-zone="main">{flash}<div>'
        html = sealed(ZONE, SEAL, f"{before}{slot}</div></main>")
        start = len(opening(ZONE, SEAL)) + len(before)
        found = list(chrome_zones(html, SEAL, (start, start + len(slot))))
        assert [name for name, _bounds in found] == ["flash"]

    def test_a_region_without_a_name_is_skipped(self) -> None:
        html = sealed(ZONE, SEAL, "<div></div>")
        assert zone_name(html, (0, len(html)), SEAL) is None
        assert list(chrome_zones(html, SEAL, None)) == []

    def test_the_injector_spells_the_same_comments(self) -> None:
        render = RouterRender(SEAL, "{}")
        assert render.sealed("x") == sealed(ASSETS_MARK, SEAL, "x")


NONCE = re.compile(r"""\snonce=(?:"[^"]*"|'[^']*')""", re.IGNORECASE)
CSRF_NAME = re.compile(r"""name=["']csrfmiddlewaretoken["']""", re.IGNORECASE)
CSRF_VALUE = re.compile(r"""(\svalue=)(?:"[^"]*"|'[^']*')""", re.IGNORECASE)


def _masked(text: str) -> str:
    """Mask `text` by the patterns the masks are defined by, each over the whole text."""
    text = NONCE.sub("", text)
    pieces: list[str] = []
    position = 0
    while (found := CSRF_NAME.search(text, position)) is not None:
        end = text.find(">", found.end())
        if end < 0:
            break
        pieces.append(text[position : found.end()])
        pieces.append(CSRF_VALUE.sub(r'\1""', text[found.end() : end], count=1))
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


class TestMasks:
    """The literal searches mask exactly what the defining patterns mask."""

    @pytest.mark.parametrize(
        "text",
        [
            'nonce="a"',
            '<script nonce="a">',
            "<script\tNONCE='a' nonce=\"b\">",
            '<script xnonce="a" nonce=b nonce= nonce="c">',
            '<script nonce="a nonce=\'b\'" nonce="c">',
            '<p>İK</p><script nonce="a">',
            'ddlewareto name="csrfmiddlewaretoken" value="t">',
            "<input NAME='CSRFMIDDLEWARETOKEN' VALUE=\"t\">",
            '<input name="xcsrfmiddlewaretoken" value="t">',
            '<input name="csrfmiddlewaretoken" value="a" name="csrfmiddlewaretoken">',
            '<p>İK</p><input name="csrfmiddlewaretoken" value="t"> value="kept"',
            '<input name="csrfmiddlewaretoken" nonce="n" value="t"><b nonce="m">',
            'name="csrfmiddlewaretoken" value="a"> name="csrfmiddlewaretoken" value="b',
        ],
    )
    def test_the_masks_follow_their_patterns(self, text) -> None:
        assert normalise(text, SEAL) == _masked(text)

    def test_text_with_neither_comes_back_as_the_object_passed(self) -> None:
        text = "<nav><a href='/docs/'>Docs</a></nav>"
        assert normalise(text, SEAL) is text


class TestFold:
    """Folding lowers ASCII letters and keeps every index where it was."""

    @pytest.mark.parametrize("text", ["<NAV Role=X>", "<P>İK</P>", "ΣΑΣ <A>", ""])
    def test_the_length_and_the_ascii_letters_hold(self, text) -> None:
        folded = fold(text)
        assert len(folded) == len(text)
        assert folded == fold(folded)
        assert not any("A" <= char <= "Z" for char in folded)

    def test_no_code_point_folds_into_an_ascii_letter(self) -> None:
        assert fold("\u0130\u212a") == "\u0131\u0138"
