from collections.abc import Callable, Iterator

import pytest
from django.conf import settings
from django.db.backends.signals import connection_created
from django.test import override_settings

from next.defer.connections import connection_sources, thread_sources
from tests.defer.connections.shared import (
    Crum,
    Role,
    StatefulRouter,
    StatelessRouter,
    Tenant,
    defer,
    dotted,
    executor,
)


@pytest.fixture()
def receiver() -> Iterator[Callable[..., None]]:
    """Connect a `connection_created` receiver the way a project would."""

    def receive(**kwargs: object) -> None:
        return None

    connection_created.connect(receive, weak=False)
    yield receive
    connection_created.disconnect(receive)


class TestConnectionDetection:
    """Per-connection state comes from receivers, routers and tenant middleware."""

    @pytest.mark.usefixtures("receiver")
    def test_a_connection_created_receiver_is_no_source(self) -> None:
        assert connection_sources() == ()

    def test_routers_without_the_stateless_flag_are_sources(
        self, deployment: Callable[..., None]
    ) -> None:
        stateless = StatelessRouter()
        stateful = StatefulRouter()
        deployment(
            routers=(
                dotted(StatelessRouter),
                dotted(StatefulRouter),
                "missing.module.Router",
                stateless,
                stateful,
            )
        )
        assert connection_sources() == (
            f"routers:{dotted(StatefulRouter)}",
            "routers:missing.module.Router",
        )

    def test_a_truthy_flag_other_than_true_keeps_the_router(
        self, deployment: Callable[..., None]
    ) -> None:
        router = StatefulRouter()
        router.next_stateless = 1
        deployment(routers=(router,))
        assert connection_sources() == (f"routers:{dotted(StatefulRouter)}",)

    @pytest.mark.parametrize(
        ("path", "found"),
        [
            ("django_tenants.middleware.main.TenantMainMiddleware", True),
            ("django_tenants.middleware.TenantSubfolderMiddleware", True),
            ("tenant_schemas.middleware.TenantMiddleware", True),
            ("django_tenants.utils.Helper", False),
            ("django_tenants_extra.middleware.Other", False),
        ],
    )
    def test_known_tenant_middleware_is_a_source(
        self, deployment: Callable[..., None], path: str, found
    ) -> None:
        deployment(middleware=(path,))
        expected = (f"tenant_middleware:{path}",) if found else ()
        assert connection_sources() == expected

    def test_a_backend_covers_a_kind_or_a_full_name(
        self, deployment: Callable[..., None]
    ) -> None:
        deployment(
            routers=(dotted(StatefulRouter),),
            middleware=("tenant_schemas.middleware.TenantMiddleware",),
        )
        tenant = "tenant_middleware:tenant_schemas.middleware.TenantMiddleware"
        for backend in (Tenant, Role):
            with defer(CONNECTION_STATE=[dotted(backend)]):
                assert connection_sources() == (tenant,)

    def test_a_middleware_change_is_read_again(self) -> None:
        path = "tenant_schemas.middleware.TenantMiddleware"
        assert connection_sources() == ()
        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, path]):
            assert connection_sources() == (f"tenant_middleware:{path}",)
            assert thread_sources() == ()
        assert connection_sources() == ()

    def test_a_thread_local_app_is_read_again(self) -> None:
        assert thread_sources() == ()
        with override_settings(MIDDLEWARE=[*settings.MIDDLEWARE, "crum.Middleware"]):
            assert thread_sources() == ("django-crum:crum.Middleware",)
        assert thread_sources() == ()


class TestThreadDetection:
    """Thread-local scoping comes from known libraries and the project's own list."""

    @pytest.mark.parametrize(
        ("where", "entry", "source"),
        [
            ("apps", "crum", "django-crum:crum"),
            (
                "middleware",
                "crum.CurrentRequestUserMiddleware",
                "django-crum:crum.CurrentRequestUserMiddleware",
            ),
            ("apps", "django_multitenant", "django-multitenant:django_multitenant"),
            ("apps", "django_scopes", "django-scopes:django_scopes"),
            (
                "apps",
                "tenant_users.tenants",
                "django-tenant-users:tenant_users.tenants",
            ),
            (
                "middleware",
                "threadlocals.middleware.ThreadLocalMiddleware",
                "django-threadlocals:threadlocals.middleware.ThreadLocalMiddleware",
            ),
            (
                "middleware",
                "django_currentuser.middleware.ThreadLocalUserMiddleware",
                (
                    "django-currentuser:"
                    "django_currentuser.middleware.ThreadLocalUserMiddleware"
                ),
            ),
            ("apps", "crumbs", None),
            ("apps", "django.contrib.auth", None),
        ],
    )
    def test_listed_libraries_are_sources(
        self,
        deployment: Callable[..., None],
        where: str,
        entry: str,
        source: str | None,
    ) -> None:
        deployment(**{where: (entry,)})
        assert thread_sources() == (() if source is None else (source,))

    def test_project_middleware_is_named_by_its_path(
        self, deployment: Callable[..., None]
    ) -> None:
        deployment()
        with executor(THREAD_LOCAL_MIDDLEWARE=["project.middleware.Tenant"]):
            assert thread_sources() == (
                "thread_local_middleware:project.middleware.Tenant",
            )

    def test_a_thread_state_covers_its_library(
        self, deployment: Callable[..., None]
    ) -> None:
        deployment(
            apps=("crum", "django_scopes"),
            middleware=("crum.CurrentRequestUserMiddleware",),
        )
        with defer(THREAD_STATE=[dotted(Crum)]):
            assert thread_sources() == ("django-scopes:django_scopes",)

    def test_the_test_project_has_no_source(self) -> None:
        assert thread_sources() == ()
