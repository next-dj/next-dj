import pytest
from django.core.exceptions import ImproperlyConfigured

from next.suspense.connections import Seams, forget_detection, seams
from tests.suspense.connections.shared import (
    Crum,
    Role,
    StatefulRouter,
    Tenant,
    defer,
    dotted,
    not_a_class,
)


class TestBackends:
    """The `THREAD_STATE` and `CONNECTION_STATE` paths load once per process."""

    def test_no_backend_is_configured_by_default(self) -> None:
        assert seams() == Seams()
        assert seams().covers == frozenset()

    def test_listed_backends_load_in_order_with_their_covers(self) -> None:
        with defer(
            THREAD_STATE=[dotted(Crum)], CONNECTION_STATE=[dotted(Tenant), dotted(Role)]
        ):
            held = seams()
            assert [type(backend) for backend in held.threads] == [Crum]
            assert [type(backend) for backend in held.connections] == [Tenant, Role]
            assert held.covers == {
                "django-crum",
                "routers",
                f"routers:{dotted(StatefulRouter)}",
            }
            assert seams() is held

    def test_a_settings_change_reads_the_backends_again(self) -> None:
        with defer(THREAD_STATE=[dotted(Crum)]):
            assert len(seams().threads) == 1
        assert seams().threads == ()

    def test_forgetting_drops_the_loaded_backends(self) -> None:
        held = seams()
        forget_detection(setting="NEXT_FRAMEWORK")
        assert seams() is not held

    def test_a_path_that_does_not_import_raises(self) -> None:
        with (
            defer(THREAD_STATE=["missing.module.State"]),
            pytest.raises(ImproperlyConfigured, match="could not be imported"),
        ):
            seams()

    @pytest.mark.parametrize(
        ("key", "path", "base"),
        [
            ("THREAD_STATE", dotted(Tenant), "ThreadState"),
            ("CONNECTION_STATE", dotted(Crum), "ConnectionState"),
            ("CONNECTION_STATE", dotted(not_a_class), "ConnectionState"),
        ],
        ids=["wrong_kind_thread", "wrong_kind_connection", "function"],
    )
    def test_a_path_to_no_subclass_raises(self, key, path, base) -> None:
        with (
            defer(**{key: [path]}),
            pytest.raises(ImproperlyConfigured, match=f"names no {base} subclass"),
        ):
            seams()

    def test_a_misfit_list_reads_as_the_default(self) -> None:
        with defer(THREAD_STATE="not.a.list"):
            assert seams().threads == ()
