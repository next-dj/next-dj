from collections.abc import Iterator

import pytest

from next.defer.payload import StreamScripts, partial_streams
from next.ports import ScriptShare, page_scripts
from next.static.runtime import BOUNDARIES_CHUNK_KEY, STREAM_PAYLOAD_KEY
from tests.support.ports import StubRender


@pytest.fixture()
def marked() -> Iterator[None]:
    token = partial_streams.set(True)
    try:
        yield
    finally:
        partial_streams.reset(token)


class TestStreamScripts:
    """A page that streams loads the reader, one that may stream partials says so."""

    def test_the_registered_provider_is_the_area_one(self) -> None:
        found = [item for item in page_scripts if item.name == "stream"]
        assert [type(item) for item in found] == [StreamScripts]
        assert found[0].applies() is True
        assert found[0].keys == {STREAM_PAYLOAD_KEY}

    def test_a_page_that_neither_streams_nor_is_marked_shares_nothing(self) -> None:
        assert StreamScripts().share(StubRender()) is None

    def test_a_streamed_page_loads_the_reader_at_once(self) -> None:
        share = StreamScripts().share(StubRender(streams=True))
        assert share == ScriptShare(eager=(BOUNDARIES_CHUNK_KEY,))

    @pytest.mark.usefixtures("marked")
    def test_a_marked_page_carries_the_flag_and_needs_the_reader(self) -> None:
        share = StreamScripts().share(StubRender())
        assert share == ScriptShare(
            shared='"$stream":true', bundles=(BOUNDARIES_CHUNK_KEY,)
        )

    @pytest.mark.usefixtures("marked")
    def test_a_marked_page_without_the_runtime_needs_no_reader(self) -> None:
        share = StreamScripts().share(StubRender(runtime=False))
        assert share == ScriptShare(shared='"$stream":true')

    @pytest.mark.usefixtures("marked")
    def test_one_combination_shares_one_object(self) -> None:
        render = StubRender(streams=True)
        assert StreamScripts().share(render) is StreamScripts().share(render)
