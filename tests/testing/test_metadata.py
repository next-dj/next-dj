import pytest
from django.http import HttpResponse

from next.testing import assert_metadata
from next.testing.metadata import head_tags


HEAD = """<html><head>
<title>Wallet &amp; Co</title>
<meta name="description" content="Money">
<meta name="robots" content="noindex, follow">
<meta name="googlebot" content="none">
<meta name="description" content="Second">
<link rel="canonical" href="https://acme.example/wallet/">
<link rel="canonical" href="https://acme.example/other/">
<link rel="alternate" hreflang="en" href="https://acme.example/wallet/">
<link rel="alternate" hreflang="x-default" href="https://acme.example/wallet/">
<link rel="alternate" type="application/rss+xml" href="/feed.xml">
<link rel="stylesheet" href="/a.css">
<meta property="og:title" content="OG Wallet">
<meta property="og:image" content="https://acme.example/a.png">
<meta name="twitter:card" content="summary">
<meta charset="utf-8">
<script type="application/ld+json">{"@type": "WebPage"}</script>
<script>var ignored = 1;</script>
</head><body><title>Not the head one</title></body></html>"""


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

    def test_every_mismatch_is_reported(self) -> None:
        with pytest.raises(AssertionError) as caught:
            assert_metadata(HEAD, title="Other", canonical=None, og={"title": "X"})
        assert str(caught.value).splitlines() == [
            "title: expected 'Other', got 'Wallet & Co'",
            "canonical: expected None, got 'https://acme.example/wallet/'",
            "og: expected {'title': 'X'}, got {'title': 'OG Wallet'}",
        ]

    def test_a_block_given_as_no_mapping_compares_to_nothing(self) -> None:
        with pytest.raises(AssertionError, match=r"og: expected 'x', got \{\}"):
            assert_metadata(HEAD, og="x")

    def test_an_unknown_key_is_refused(self) -> None:
        with pytest.raises(TypeError, match=r"unknown keys \['icons'\]"):
            assert_metadata(HEAD, icons="a")

    def test_the_keywords_and_the_viewport_read_as_names(self) -> None:
        head = (
            '<meta name="keywords" content="a, b">'
            '<meta name="viewport" content="width=device-width">'
        )
        assert_metadata(head, keywords="a, b", viewport="width=device-width")

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
