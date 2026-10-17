import pytest

from next.boundaries.canvas import Dependency, ModelEntry, canvas
from next.boundaries.errors import UnknownAppError
from next.boundaries.graph import Graph
from next.boundaries.manager import Scan, boundaries_manager
from tests.support import isolated_form_registries, write_urlconf
from tests.support.access import login_required_middleware
from tests.support.boundaries import CANVAS_APPS, boundary_project


@pytest.fixture()
def canvas_project(tmp_path):
    urlconf = write_urlconf(tmp_path, "canvas_urls", 'path("", include("next.urls"))')
    with (
        isolated_form_registries(),
        boundary_project(tmp_path, CANVAS_APPS, urlconf=urlconf),
    ):
        yield


@pytest.fixture()
def shop_canvas(canvas_project):
    scan = boundaries_manager.scan()
    return canvas(scan, "shop"), canvas(scan, "crm"), scan


class TestCanvas:
    """The canvas of an app says what it owns, publishes, listens to and leans on."""

    def test_models_with_their_relations(self, shop_canvas) -> None:
        shop, crm, _scan = shop_canvas

        assert shop.models == (
            ModelEntry("Cart", ()),
            ModelEntry("Order", ("Order.buyer -> crm.Client",)),
        )
        assert crm.models == (ModelEntry("Client", ()),)

    def test_signals_and_services(self, shop_canvas) -> None:
        shop, crm, _scan = shop_canvas

        assert shop.signals == ("placed",)
        assert shop.services == ("place_order", "refund")
        assert crm.signals == ("changed",)
        assert crm.services == ("billing.bill",)

    def test_listens_and_keys(self, shop_canvas) -> None:
        shop, crm, _scan = shop_canvas

        assert [(edge.target, edge.detail) for edge in shop.listens] == [
            ("crm", "crm.signals.changed")
        ]
        assert [item.key for item in shop.keys] == ["shop_cart"]
        assert crm.listens == ()
        assert crm.keys == ()

    def test_pages_and_risks(self, shop_canvas) -> None:
        shop, crm, _scan = shop_canvas

        assert [(item.kind, item.name, item.access) for item in shop.pages] == [
            ("page", "/", "public"),
            ("page", "/orders/", "-"),
            ("action", "cancel", "login"),
            ("action", "place", "-"),
        ]
        assert [item.name for item in shop.risks] == ["place"]
        assert crm.pages == ()

    @pytest.mark.usefixtures("canvas_project")
    def test_an_action_the_middleware_closes_is_no_risk(self) -> None:
        with login_required_middleware():
            shop = canvas(boundaries_manager.scan(), "shop")

        assert shop.risks == ()

    def test_dependencies_both_ways(self, shop_canvas) -> None:
        shop, crm, _scan = shop_canvas

        assert shop.depends_on == (
            Dependency("crm", (("fk", 1), ("import", 1), ("signal", 1))),
        )
        assert shop.used_by == (Dependency("crm", (("import", 1),)),)
        assert crm.depends_on == (Dependency("shop", (("import", 1),)),)
        assert crm.used_by == (
            Dependency("shop", (("fk", 1), ("import", 1), ("signal", 1))),
        )

    def test_an_unknown_label(self, shop_canvas) -> None:
        _shop, _crm, scan = shop_canvas

        with pytest.raises(UnknownAppError, match="'nope' is no application") as raised:
            canvas(scan, "nope")

        assert str(raised.value).endswith("Its applications: crm, shop.")
        assert raised.value.label == "nope"

    def test_an_empty_scan_knows_no_app(self) -> None:
        empty = Scan(None, Graph((), ()), (), ())

        with pytest.raises(UnknownAppError, match=r"Its applications: none\."):
            canvas(empty, "shop")
