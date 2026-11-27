from next.requests import PrefetchDepth, RequestKind


def test_every_kind_has_one_spelling_on_the_wire() -> None:
    assert [kind.value for kind in RequestKind] == [
        "page",
        "action",
        "zone",
        "boundary",
        "navigate",
    ]


def test_a_prefetch_asks_for_the_shell_or_the_full_page() -> None:
    assert [depth.value for depth in PrefetchDepth] == ["shell", "full"]
