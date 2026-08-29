from __future__ import annotations

import pytest

from tests.support import MockAutoreloadSender


@pytest.fixture()
def mock_autoreload_sender() -> MockAutoreloadSender:
    """Minimal sender stub for ``autoreload_started`` signal tests."""
    return MockAutoreloadSender()
