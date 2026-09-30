from next.pages.metadata.head import HeadTags, head_tags


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


class TestHeadTags:
    """`head_tags` reads the first value of every metadata tag."""

    def test_the_tags_are_collected(self) -> None:
        assert head_tags(HEAD) == HeadTags(
            title="Wallet & Co",
            names={
                "description": "Money",
                "robots": "noindex, follow",
                "googlebot": "none",
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
            robots=["noindex, follow"],
        )

    def test_an_empty_document_has_no_tags(self) -> None:
        assert head_tags("") == HeadTags()

    def test_every_robots_meta_is_listed_and_names_read_lower_case(self) -> None:
        tags = head_tags(
            '<META NAME="Robots" content="noindex"><meta name="robots">'
            '<meta name="robots" content="nofollow">'
        )
        assert tags.robots == ["noindex", "nofollow"]
        assert tags.names == {"robots": "noindex"}

    def test_a_json_ld_body_that_is_no_json_is_kept_as_text(self) -> None:
        tags = head_tags('<script type="application/ld+json">{oops</script>')
        assert tags.jsonld == ["{oops"]
