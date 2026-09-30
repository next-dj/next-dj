from next.pages.checks.metadata.links import url_fields
from next.pages.metadata import (
    Alternates,
    Feed,
    Icon,
    Link,
    Metadata,
    OpenGraph,
    OpenGraphAudio,
    OpenGraphImage,
    OpenGraphVideo,
    Twitter,
    TwitterImage,
    TwitterPlayer,
)


class TestUrlFields:
    """Every URL a fold carries is named by its dotted path, links first."""

    def test_every_url_field_is_named(self) -> None:
        meta = Metadata(
            canonical="/c",
            manifest="/m.json",
            links=(Link("me", "/me"),),
            alternates=Alternates(
                languages=(("de", "/de/"),),
                x_default="/",
                feeds=(Feed("/f.xml", "rss"),),
            ),
            og=OpenGraph(
                url="/o",
                images=(OpenGraphImage(url="/i.png", secure_url="/s.png"),),
                videos=(OpenGraphVideo("/v.mp4"),),
                audio=(OpenGraphAudio("/a.mp3", secure_url="/sa.mp3"),),
            ),
            twitter=Twitter(
                images=(TwitterImage("/t.png"),),
                player=TwitterPlayer("/p", 1, 1, "/stream"),
            ),
            icons=(Icon("icon", "/icon.svg"),),
        )
        assert list(url_fields(meta)) == [
            ("canonical", "/c"),
            ("og.url", "/o"),
            ("manifest", "/m.json"),
            ("links[0].href", "/me"),
            ("alternates.x_default", "/"),
            ("alternates.languages.de", "/de/"),
            ("alternates.feeds[0].url", "/f.xml"),
            ("og.images[0].url", "/i.png"),
            ("og.images[0].secure_url", "/s.png"),
            ("og.videos[0].url", "/v.mp4"),
            ("og.audio[0].url", "/a.mp3"),
            ("og.audio[0].secure_url", "/sa.mp3"),
            ("twitter.images[0].url", "/t.png"),
            ("twitter.player.url", "/p"),
            ("twitter.player.stream", "/stream"),
            ("icons[0].url", "/icon.svg"),
        ]

    def test_a_player_without_a_stream_names_only_its_url(self) -> None:
        meta = Metadata(twitter=Twitter(player=TwitterPlayer("/p", 1, 1)))
        assert list(url_fields(meta)) == [("twitter.player.url", "/p")]
