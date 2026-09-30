import pytest

from next.scripts import Script, Strategy
from next.scripts.render import head_tags, inline_body, manifest_entry


class TestInlineBody:
    """An `init` body cannot close the element it sits in."""

    @pytest.mark.parametrize(
        ("body", "safe"),
        [
            ("a('</script>')", "a('<\\/script>')"),
            ("a('</SCRIPT')", "a('<\\/SCRIPT')"),
            ("a('</div>')", "a('</div>')"),
        ],
    )
    def test_a_closing_tag_is_broken(self, body: str, safe: str) -> None:
        assert inline_body(body) == safe


class TestHeadTags:
    """A head script renders its `init` first, both tags naming the script."""

    def test_an_init_and_a_src_with_every_attribute(self) -> None:
        script = Script(
            "chat",
            src="https://w.example/chat.js",
            init="window.q=[]",
            strategy=Strategy.DEFER,
            attrs={"integrity": "sha384-x", "onload": "x"},
        )
        assert head_tags(script, "https://w.example/chat.js?a=1&b=2", "n0") == (
            '<script nonce="n0" data-next-script="chat">window.q=[]</script>\n'
            '<script src="https://w.example/chat.js?a=1&amp;b=2" defer '
            'integrity="sha384-x" nonce="n0" data-next-script="chat"></script>'
        )

    def test_a_blocking_src_carries_no_load_attribute(self) -> None:
        script = Script("a", src="/a.js", strategy=Strategy.BLOCKING)
        assert head_tags(script, "/a.js", None) == (
            '<script src="/a.js" data-next-script="a"></script>'
        )

    def test_an_async_src(self) -> None:
        script = Script("a", src="/a.js")
        assert head_tags(script, "/a.js", None) == (
            '<script src="/a.js" async data-next-script="a"></script>'
        )

    def test_an_init_alone(self) -> None:
        script = Script("a", init="x()</script>")
        assert head_tags(script, None, None) == (
            '<script data-next-script="a">x()<\\/script></script>'
        )


class TestManifestEntry:
    """A manifest entry carries the keys the runtime reads, optional ones if set."""

    def test_the_full_entry(self) -> None:
        script = Script(
            "pixel",
            src="https://p.example/p.js",
            init="p()",
            strategy=Strategy.IDLE,
            category="marketing",
            attrs={"data-id": "7", "src": "x"},
        )
        assert manifest_entry(script, "https://p.example/p.js", "n0") == {
            "name": "pixel",
            "src": "https://p.example/p.js",
            "init": "p()",
            "strategy": "idle",
            "category": "marketing",
            "attrs": {"data-id": "7"},
            "nonce": "n0",
        }

    def test_the_minimal_entry(self) -> None:
        script = Script("a", init="a()", strategy=Strategy.MANUAL)
        assert manifest_entry(script, None, None) == {
            "name": "a",
            "init": "a()",
            "strategy": "manual",
            "category": "necessary",
            "attrs": {},
        }
