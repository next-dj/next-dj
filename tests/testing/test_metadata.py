import pytest
from django.http import HttpResponse, StreamingHttpResponse

from next.testing import assert_metadata
from next.testing.metadata import HeadParseError, HeadTags, head_tags


HEAD = """<html><head>
<title>Wallet &amp; Co</title>
<meta name="description" content="Money">
<meta name="robots" content="noindex, follow">
<meta name="googlebot" content="none">
<meta name="google-site-verification" content="a">
<meta name="google-site-verification" content="b">
<link rel="canonical" href="https://acme.example/wallet/">
<link rel="alternate" hreflang="en" href="https://acme.example/wallet/">
<link rel="alternate" hreflang="x-default" href="https://acme.example/wallet/">
<link rel="alternate" type="application/rss+xml" href="/feed.xml">
<link rel="stylesheet" href="/a.css">
<meta property="og:title" content="OG Wallet">
<meta property="og:image" content="https://acme.example/a.png">
<meta property="og:image" content="https://acme.example/b.png">
<meta name="twitter:card" content="summary">
<meta charset="utf-8">
<script type="application/ld+json">{"@type": "WebPage"}</script>
<script>var ignored = 1;</script>
</head><body><title>Not the head one</title>
<link rel="canonical" href="https://acme.example/body/">
<script type="application/ld+json">{oops</script></body></html>"""


class TestHeadTags:
    """`head_tags` reads the head strictly, up to its end."""

    def test_the_tags_are_collected(self) -> None:
        assert head_tags(HEAD) == HeadTags(
            title="Wallet & Co",
            names={
                "description": "Money",
                "robots": "noindex, follow",
                "googlebot": "none",
                "google-site-verification": "a",
                "twitter:card": "summary",
            },
            properties={
                "og:title": "OG Wallet",
                "og:image": "https://acme.example/a.png",
            },
            canonical="https://acme.example/wallet/",
            alternates={
                "en": "https://acme.example/wallet/",
                "x-default": "https://acme.example/wallet/",
            },
            jsonld=[{"@type": "WebPage"}],
        )

    def test_an_empty_document_has_no_tags(self) -> None:
        assert head_tags("") == HeadTags()

    def test_names_read_lower_case(self) -> None:
        tags = head_tags('<META NAME="Robots" content="noindex">')
        assert tags.names == {"robots": "noindex"}

    @pytest.mark.parametrize(
        ("html", "fragment"),
        [
            ("<title>A</title><title>B</title>", "two titles"),
            (
                '<link rel="canonical" href="/a"><link rel="canonical" href="/b">',
                "two canonical links",
            ),
            ('<meta name="robots" content="noindex">' * 2, "'robots' twice"),
            ('<meta property="og:title" content="A">' * 2, "'og:title' twice"),
            ('<link rel="alternate" hreflang="de" href="/a">' * 2, "'de' twice"),
            ('<script type="application/ld+json">{oops</script>', "holds no JSON"),
        ],
        ids=["title", "canonical", "robots", "og_title", "hreflang", "jsonld"],
    )
    def test_a_head_a_crawler_trips_on_raises(self, html: str, fragment: str) -> None:
        with pytest.raises(HeadParseError, match=fragment):
            head_tags(html)

    def test_a_graph_script_lists_its_nodes(self) -> None:
        head = (
            '<script type="application/ld+json">{"@context": "https://schema.org", '
            '"@graph": [{"@type": "WebSite"}, {"@type": "Organization"}]}</script>'
            '<script type="application/ld+json">{"@type": "Custom"}</script>'
        )
        assert head_tags(head).jsonld == [
            {"@type": "WebSite"},
            {"@type": "Organization"},
            {"@type": "Custom"},
        ]


class TestAssertMetadata:
    """`assert_metadata` compares the expected tags, `None` for an absent one."""

    def test_matching_tags_pass(self) -> None:
        assert_metadata(
            HttpResponse(HEAD),
            title="Wallet & Co",
            description="Money",
            robots="noindex, follow",
            googlebot="none",
            canonical="https://acme.example/wallet/",
            og={"title": "OG Wallet", "type": None},
            twitter={"card": "summary"},
            alternates={
                "en": "https://acme.example/wallet/",
                "x-default": "https://acme.example/wallet/",
            },
            jsonld=({"@type": "WebPage"},),
        )

    def test_a_string_is_read_as_the_document(self) -> None:
        assert_metadata("<title>T</title>", title="T", description=None)

    def test_none_expects_a_whole_block_to_be_absent(self) -> None:
        assert_metadata(
            "<title>T</title>", og=None, twitter=None, alternates=None, jsonld=None
        )
        with pytest.raises(AssertionError, match=r"og: expected None, got \{"):
            assert_metadata(HEAD, og=None)

    def test_every_mismatch_is_reported(self) -> None:
        with pytest.raises(AssertionError) as caught:
            assert_metadata(HEAD, title="Other", canonical=None, og={"title": "X"})
        assert str(caught.value).splitlines() == [
            "title: expected 'Other', got 'Wallet & Co'",
            "canonical: expected None, got 'https://acme.example/wallet/'",
            "og: expected {'title': 'X'}, got {'title': 'OG Wallet'}",
        ]

    def test_a_block_given_as_no_mapping_compares_to_the_whole_block(self) -> None:
        with pytest.raises(AssertionError, match=r"twitter: expected 'x', got \{"):
            assert_metadata(HEAD, twitter="x")

    def test_an_unknown_key_is_refused(self) -> None:
        with pytest.raises(TypeError, match=r"unknown keys \['icons'\]"):
            assert_metadata(HEAD, icons="a")

    def test_the_keywords_and_the_viewport_read_as_names(self) -> None:
        head = (
            '<meta name="keywords" content="a, b">'
            '<meta name="viewport" content="width=device-width">'
        )
        assert_metadata(head, keywords="a, b", viewport="width=device-width")

    def test_the_response_charset_decodes_the_body(self) -> None:
        body = "<title>Café</title>".encode("latin-1")
        response = HttpResponse(body, content_type="text/html; charset=latin-1")
        assert_metadata(response, title="Café")

    def test_a_streamed_response_is_read_to_its_end(self) -> None:
        response = StreamingHttpResponse(iter([b"<head><title>", b"T</title>"]))
        assert_metadata(response, title="T")
