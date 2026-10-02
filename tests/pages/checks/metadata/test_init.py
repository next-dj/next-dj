from django.core.checks.registry import registry as check_registry

import next.checks as facade
from next.checks import register_all
from next.pages.checks import metadata


CHECKS = [getattr(metadata, name) for name in metadata.__all__]


class TestMetadataChecks:
    """Every metadata check is reachable through `next.checks` and registered."""

    def test_every_check_is_reachable_through_next_checks(self) -> None:
        assert set(metadata.__all__) <= set(facade.__all__)
        assert all(getattr(facade, check.__name__) is check for check in CHECKS)

    def test_every_check_is_registered(self) -> None:
        register_all()
        registered = check_registry.registered_checks | check_registry.deployment_checks
        assert set(CHECKS) <= registered
