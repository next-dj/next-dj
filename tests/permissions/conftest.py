import pytest

from next.permissions.manager import PermissionManager


@pytest.fixture(autouse=True)
def _isolated_registries(
    isolated_permissions: PermissionManager, form_registries: None
) -> None:
    """Run each test of the area over empty permission and form action registries."""
