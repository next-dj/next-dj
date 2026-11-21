import pytest

from next.router import (
    ChromeDigest,
    Cut,
    CutKind,
    Declaration,
    Divergence,
    LayoutKey,
    VerifiedCut,
)
from next.router.markers import ScannedLayout, Shell


ROOT = "1" * 16
SECTION = "2" * 16
LEAF = "3" * 16
HEAD = "a" * 16
BODY = "b" * 16


def _layout(ident: str, digest: str, head: str | None = None) -> ScannedLayout:
    return ScannedLayout(ident, digest, (0, 0), (0, 0), head)


SHELL = Shell(
    "",
    "5ea1",
    (
        _layout(ROOT, HEAD + BODY, HEAD),
        _layout(SECTION, "c" * 16),
        _layout(LEAF, "d" * 16),
    ),
)


def _declared(*pairs: tuple[str, str]) -> Declaration:
    return Declaration(tuple(LayoutKey(ident, digest) for ident, digest in pairs))


class TestVerifiedCut:
    """The cut keeps the deepest layout whose id and digest match from the root."""

    @pytest.mark.parametrize(
        ("declared", "expected"),
        [
            pytest.param(
                _declared((ROOT, HEAD + BODY), (SECTION, "c" * 16), (LEAF, "d" * 16)),
                Cut(CutKind.SLOT, (ROOT, SECTION, LEAF)),
                id="every-layout-matches",
            ),
            pytest.param(
                _declared((ROOT, HEAD + BODY), (SECTION, "c" * 16), (LEAF, "e" * 16)),
                Cut(CutKind.SLOT, (ROOT, SECTION), LEAF, Divergence.DIGEST),
                id="deepest-match",
            ),
            pytest.param(
                _declared((ROOT, HEAD + BODY), ("4" * 16, "c" * 16)),
                Cut(CutKind.SLOT, (ROOT,), SECTION, Divergence.ID),
                id="partial-match-by-id",
            ),
            pytest.param(
                _declared((ROOT, HEAD + BODY)),
                Cut(CutKind.SLOT, (ROOT,), SECTION, Divergence.DECLARATION),
                id="shorter-declaration",
            ),
            pytest.param(
                _declared((ROOT, HEAD + "f" * 16)),
                Cut(CutKind.BODY, (), ROOT, Divergence.DIGEST),
                id="body-morph",
            ),
            pytest.param(
                _declared((ROOT, "f" * 32)),
                Cut(CutKind.VISIT, (), ROOT, Divergence.HEAD),
                id="head-differs",
            ),
            pytest.param(
                _declared(("9" * 16, HEAD + BODY)),
                Cut(CutKind.VISIT, (), ROOT, Divergence.ID),
                id="root-differs",
            ),
            pytest.param(
                _declared(),
                Cut(CutKind.VISIT, (), ROOT, Divergence.ID),
                id="no-layout-declared",
            ),
            pytest.param(
                None,
                Cut(CutKind.BODY, (), ROOT, Divergence.DECLARATION),
                id="no-declaration",
            ),
        ],
    )
    def test_the_rung(self, declared: Declaration | None, expected: Cut) -> None:
        assert VerifiedCut().cut(declared, SHELL) == expected

    def test_a_page_without_layouts_morphs_the_body(self) -> None:
        bare = Shell("", "5ea1", ())
        assert VerifiedCut().cut(_declared(), bare) == Cut(CutKind.BODY)
        assert VerifiedCut().cut(_declared((ROOT, BODY)), bare).kind is CutKind.VISIT

    def test_a_root_without_a_head_morphs_the_body(self) -> None:
        shell = Shell("", "5ea1", (_layout(ROOT, BODY),))
        cut = VerifiedCut().cut(_declared((ROOT, "f" * 16)), shell)
        assert cut.kind is CutKind.BODY

    def test_the_slot_is_the_deepest_kept_layout(self) -> None:
        assert Cut(CutKind.SLOT, (ROOT, SECTION)).slot == SECTION
        assert Cut(CutKind.BODY).slot is None


class TestChromeDigest:
    """The default digest normalises, then hashes."""

    def test_normalise_and_digest(self) -> None:
        digest = ChromeDigest()
        text = digest.normalise('<p nonce="x">a</p>', "5ea1")
        assert text == "<p>a</p>"
        assert len(digest.digest(text)) == 16
