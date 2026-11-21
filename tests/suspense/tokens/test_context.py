from next.suspense.tokens import VERSION
from next.suspense.tokens.context import TokenContext
from tests.support.tokens import KEY


class _Counted:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return self.value


class TestTokenContext:
    """A callable ring, digest or room is read once, and only when first asked for."""

    def test_plain_values_read_as_they_are(self) -> None:
        context = TokenContext(
            version=VERSION, epoch=1, ring=(KEY,), digest="d", room=12
        )
        assert (context.keys, context.binding, context.inline_bytes) == (
            (KEY,),
            "d",
            12,
        )
        assert context.purpose == f"next.defer.boundary.v{VERSION}.1"

    def test_callables_wait_for_the_first_read_and_run_once(self) -> None:
        ring, digest, room = _Counted((KEY,)), _Counted("d"), _Counted(7)
        context = TokenContext(
            version=VERSION, epoch=1, ring=ring, digest=digest, room=room
        )
        assert (ring.calls, digest.calls, room.calls) == (0, 0, 0)
        for _read in range(3):
            assert (context.keys, context.binding, context.inline_bytes) == (
                (KEY,),
                "d",
                7,
            )
        assert (ring.calls, digest.calls, room.calls) == (1, 1, 1)

    def test_the_defaults_bind_nothing_and_inline_nothing(self) -> None:
        context = TokenContext(version=VERSION, epoch=1, ring=(KEY,))
        assert (context.binding, context.inline_bytes, context.lifetime) == ("", 0, 0)
        assert (context.max_stored_bytes, context.cache_alias) == (65_536, "default")
