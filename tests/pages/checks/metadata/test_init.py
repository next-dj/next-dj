from django.core.checks.registry import registry as check_registry

from next.pages.checks import metadata


CHECKS = [getattr(metadata, name) for name in metadata.__all__]


class TestMetadataChecks:
    """Every metadata check registers with Django."""

    def test_every_check_is_registered(self) -> None:
        registered = check_registry.registered_checks | check_registry.deployment_checks
        assert set(CHECKS) <= registered
