import pytest

from next.ports import RouterAccess
from next.urls import FileRouterBackend, RouterManager, URLPatternParser
from next.urls.ports import RouterAccessImpl
from tests.support import call_shape, file_router_config_entry, port_methods


class TestRouterAccessPort:
    """The router port builds exactly what the urls area would build itself."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert port_methods(RouterAccess) == [
            "create_backend",
            "create_manager",
            "url_parser",
        ]

    @pytest.mark.parametrize("name", ["create_backend", "create_manager", "url_parser"])
    def test_implementation_parameters_match_the_port(self, name) -> None:
        assert call_shape(RouterAccessImpl, name) == call_shape(RouterAccess, name)

    def test_create_backend_builds_the_router_the_entry_names(self, tmp_path) -> None:
        backend = RouterAccessImpl().create_backend(
            file_router_config_entry(pages_dir=tmp_path)
        )
        assert isinstance(backend, FileRouterBackend)

    def test_create_manager_builds_a_fresh_manager(self) -> None:
        access = RouterAccessImpl()
        first = access.create_manager()
        assert isinstance(first, RouterManager)
        assert access.create_manager() is not first

    def test_url_parser_is_the_shared_one_the_file_router_routes_through(self) -> None:
        """One parser, so a caller off the port reads the same memoised patterns."""
        access = RouterAccessImpl()
        parser = access.url_parser()

        assert isinstance(parser, URLPatternParser)
        assert access.url_parser() is parser
