import re

import pytest

from next.urls.regex import Portable, translate


_SAMPLES = (
    "",
    "a",
    "ab",
    "abc/",
    "a/b/",
    "2024/",
    "2024/12/",
    "x-y_z/",
    "a.b",
    "a]b}",
    "A/",
    "é/",
    "a" * 9,
    "en/",
    "de/x/",
    "{}",
    "-",
)


@pytest.mark.parametrize(
    ("regex", "translated"),
    [
        pytest.param("", Portable("", start=False, end=False), id="empty"),
        pytest.param("^a/$", Portable("a/", start=True, end=True), id="anchors"),
        pytest.param(
            r"\Aa/\Z", Portable("a/", start=True, end=True), id="string-anchors"
        ),
        pytest.param(
            r"^a\.b\$$", Portable(r"a\.b\$", start=True, end=True), id="escapes"
        ),
        pytest.param("^a.c$", Portable("a.c", start=True, end=True), id="dot"),
        pytest.param(
            "^[0-9]{4}/$", Portable("[0-9]{4}/", start=True, end=True), id="class"
        ),
        pytest.param(
            r"^[^/\]-]+$", Portable(r"[^\/\]\-]+", start=True, end=True), id="negated"
        ),
        pytest.param("^[a-z-]$", Portable(r"[a-z\-]", start=True, end=True), id="dash"),
        pytest.param("^[a^.]$", Portable(r"[a\^\.]", start=True, end=True), id="punct"),
        pytest.param(
            "^(?P<y>[0-9]+)/(x|y)/$",
            Portable("(?:[0-9]+)/(?:x|y)/", start=True, end=True),
            id="groups",
        ),
        pytest.param(
            "^(?:en|de)?/a{2,}b{1,3}?/c*d+?$",
            Portable("(?:en|de)?/a{2,}b{1,3}?/c*d+?", start=True, end=True),
            id="quantifiers",
        ),
        pytest.param("a|b", Portable("a|b", start=False, end=False), id="free-fork"),
        pytest.param(
            "^.+/[^a]*/(?:b)+?/.{2,}/a+/.?$",
            Portable(".+/[^a]*/(?:b)+?/.{2,}/a+/.?", start=True, end=True, spans=4),
            id="spans",
        ),
        pytest.param("^a]}$", Portable(r"a\]\}", start=True, end=True), id="closers"),
        pytest.param("^(?:a?)?$", Portable("(?:a?)?", start=True, end=True), id="once"),
        pytest.param(r"^a/\d+/$", None, id="digit"),
        pytest.param(r"^\w+$", None, id="word"),
        pytest.param(r"^\s$", None, id="space"),
        pytest.param(r"^a\b$", None, id="boundary"),
        pytest.param(r"^[\D]$", None, id="class-escape"),
        pytest.param(r"^(a)\1$", None, id="backreference"),
        pytest.param("^(?P<a>x)(?P=a)$", None, id="named-backreference"),
        pytest.param("^a(?=b)$", None, id="lookahead"),
        pytest.param("^(?<=a)b$", None, id="lookbehind"),
        pytest.param("(?i)^a$", None, id="flags"),
        pytest.param("^(?(1)a|b)$", None, id="conditional"),
        pytest.param("^(?>a)$", None, id="atomic"),
        pytest.param("^a++$", None, id="possessive"),
        pytest.param("^a{2}+$", None, id="possessive-bounds"),
        pytest.param(r"^\x41$", None, id="hex"),
        pytest.param(r"^\101$", None, id="octal"),
        pytest.param(r"^\u0041$", None, id="unicode"),
        pytest.param("^[]a]$", None, id="leading-bracket"),
        pytest.param("^[^]a]$", None, id="negated-leading-bracket"),
        pytest.param("^é$", None, id="non-ascii"),
        pytest.param("^[é]$", None, id="non-ascii-class"),
        pytest.param("^a\tb$", None, id="control"),
        pytest.param("^a$b", None, id="inner-end"),
        pytest.param("a^b", None, id="inner-start"),
        pytest.param("^(a$)", None, id="grouped-end"),
        pytest.param("^a|b$", None, id="anchored-fork"),
        pytest.param("^a{,3}$", None, id="open-low"),
        pytest.param("^a{x}$", None, id="literal-brace"),
        pytest.param("^a{2$", None, id="unclosed-brace"),
        pytest.param("^*a$", None, id="nothing-to-repeat"),
        pytest.param("^(?:a+)+$", None, id="nested-repeat"),
        pytest.param("^[a-z]+[a-z]*[0-9]{2,}$", None, id="crowded-segment"),
        pytest.param(
            r"^[a-z]+[0-9]*/[a-z]+\/[a-z]+/$",
            Portable(r"[a-z]+[0-9]*/[a-z]+\/[a-z]+/", start=True, end=True),
            id="repeats-apart",
        ),
        pytest.param("^(?:a|b)*$", None, id="repeated-fork"),
        pytest.param("^[a-b-c]$", None, id="chained-range"),
        pytest.param("^[a--]$", None, id="set-difference"),
        pytest.param("^[--a]$", None, id="leading-set-difference"),
        pytest.param("^[a&&b]$", None, id="set-intersection"),
        pytest.param("^[a[]$", None, id="nested-class"),
        pytest.param("^[a$", None, id="unclosed-class"),
        pytest.param("^(?P<1>a)$", None, id="bad-name"),
        pytest.param("^(?P<a$", None, id="unclosed-name"),
        pytest.param("^(a$", None, id="unclosed-group"),
        pytest.param("^a)$", None, id="stray-close"),
        pytest.param("^a\\", None, id="trailing-backslash"),
    ],
)
def test_each_regex_translates_or_is_refused(
    regex: str, translated: Portable | None
) -> None:
    assert translate(regex) == translated


@pytest.mark.parametrize(
    "regex",
    [
        "^a/$",
        "^[0-9]{4}/$",
        r"^[^/\]-]+$",
        "^[a-z-]+/$",
        "^(?P<y>[0-9]+)/(x|y)/$",
        "^(?:en|de)?/?[a-z]{2,}$",
        r"^a\.b$",
        "^a]}$",
        "^.+$",
        "^[{}]+$",
        "^-$",
    ],
)
def test_a_translation_matches_what_python_matches(regex: str) -> None:
    found = translate(regex)
    assert found is not None
    portable = re.compile(found.source)
    original = re.compile(regex)
    for sample in _SAMPLES:
        assert (portable.fullmatch(sample) is None) is (
            original.fullmatch(sample) is None
        ), sample
