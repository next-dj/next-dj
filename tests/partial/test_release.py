import json

from next.partial.release import ReleaseScripts
from next.partial.stream import PROTOCOL
from next.ports import page_scripts
from next.static.runtime import PROTOCOL_PAYLOAD_KEY, VERSION_PAYLOAD_KEY
from next.testing import override_next_settings
from tests.support.ports import StubRender


class TestReleaseScripts:
    """Every runtime page names the asset version and the protocol it was built for."""

    def test_the_registered_provider_is_the_area_one(self) -> None:
        found = [item for item in page_scripts if item.name == "release"]
        assert [type(item) for item in found] == [ReleaseScripts]
        assert found[0].applies() is True
        assert found[0].keys == {VERSION_PAYLOAD_KEY, PROTOCOL_PAYLOAD_KEY}

    def test_the_share_names_the_version_and_the_protocol(self) -> None:
        with override_next_settings(STATIC_VERSION="deploy-7"):
            share = ReleaseScripts().share(StubRender())
        decoded = json.loads(f"{{{share.shared}}}")
        assert decoded == {"$version": "deploy-7", "$protocol": PROTOCOL}
        assert list(decoded) == [VERSION_PAYLOAD_KEY, PROTOCOL_PAYLOAD_KEY]

    def test_one_version_shares_one_object(self) -> None:
        render = StubRender()
        assert ReleaseScripts().share(render) is ReleaseScripts().share(render)
