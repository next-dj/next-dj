"""
Tests for dependency injection in next.dj framework.

These tests define how DependencyResolver should inject dependencies into
context and render functions based on type hints and parameter names.
"""

from unittest.mock import MagicMock

import pytest
from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest

from next.dependencies import DependencyResolver


# test helper functions that accept various dependencies
def needs_request(request: HttpRequest) -> str:
    """Function that expects request injection by type hint."""
    return f"method={request.method}"


def needs_session(session: SessionBase) -> str:
    """Function that expects session injection by type hint."""
    return f"key={session.session_key}"


def needs_user(user) -> str:
    """Function that expects user injection by parameter name only."""
    return f"name={user.username}"


def needs_multiple(request: HttpRequest, session: SessionBase, user) -> tuple:
    """Function that expects all three dependencies injected."""
    return (request, session, user)


def needs_explicit_override(request: HttpRequest, custom: str) -> tuple:
    """Function where explicit kwargs should override injected values."""
    return (request, custom)


def needs_unsupported_type(value: int) -> int:
    """Function with unsupported type that should be skipped."""
    return value


def needs_nothing() -> str:
    """Function with no dependencies should get empty dict."""
    return "ok"


@pytest.fixture
def mock_request():
    """Create a mock HttpRequest for testing."""
    req = MagicMock(spec=HttpRequest)
    req.method = "GET"
    req.path = "/test"
    return req


@pytest.fixture
def mock_session():
    """Create a mock SessionBase for testing."""
    sess = MagicMock(spec=SessionBase)
    sess.session_key = "abc123"
    return sess


@pytest.fixture
def mock_user():
    """Create a mock user object without importing django User."""
    user = MagicMock()
    user.username = "testuser"
    user.is_authenticated = True
    return user


@pytest.fixture
def dependencies(mock_request, mock_session, mock_user):
    """Provide a standard set of available dependencies."""
    return {
        "request": mock_request,
        "session": mock_session,
        "user": mock_user,
    }


@pytest.fixture
def resolver():
    """Create a fresh DependencyResolver instance."""
    return DependencyResolver()


def test_inject_request_by_type(resolver, dependencies):
    """Request should be injected when parameter has HttpRequest type hint."""
    result = resolver.resolve(needs_request, dependencies, {})
    assert "request" in result
    assert result["request"] is dependencies["request"]
    assert needs_request(**result) == "method=GET"


def test_inject_session_by_type(resolver, dependencies):
    """Session should be injected when parameter has SessionBase type hint."""
    result = resolver.resolve(needs_session, dependencies, {})
    assert "session" in result
    assert result["session"] is dependencies["session"]
    assert needs_session(**result) == "key=abc123"


def test_inject_user_by_name(resolver, dependencies):
    """User should be injected by parameter name without type annotation."""
    result = resolver.resolve(needs_user, dependencies, {})
    assert "user" in result
    assert result["user"] is dependencies["user"]
    assert needs_user(**result) == "name=testuser"


def test_inject_multiple_dependencies(resolver, dependencies):
    """All available dependencies should be injected correctly."""
    result = resolver.resolve(needs_multiple, dependencies, {})
    assert len(result) == 3
    assert result["request"] is dependencies["request"]
    assert result["session"] is dependencies["session"]
    assert result["user"] is dependencies["user"]


def test_explicit_kwargs_override_injection(resolver, dependencies):
    """Explicit kwargs should take precedence over injected values."""
    custom_request = MagicMock(spec=HttpRequest)
    custom_request.method = "POST"

    explicit = {"request": custom_request, "custom": "value"}
    result = resolver.resolve(needs_explicit_override, dependencies, explicit)

    assert result["request"] is custom_request
    assert result["request"] is not dependencies["request"]
    assert result["custom"] == "value"


def test_unsupported_types_skipped(resolver, dependencies):
    """Parameters with unsupported types should not be injected."""
    result = resolver.resolve(needs_unsupported_type, dependencies, {})
    assert "value" not in result
    assert result == {}


def test_no_dependencies_returns_empty(resolver, dependencies):
    """Functions with no parameters should get an empty dict."""
    result = resolver.resolve(needs_nothing, dependencies, {})
    assert result == {}
    assert needs_nothing(**result) == "ok"


def test_caching_per_function(resolver, dependencies):
    """Resolved dependencies should be cached per function."""
    result1 = resolver.resolve(needs_request, dependencies, {})

    # second call should return the exact same dict instance
    result2 = resolver.resolve(needs_request, dependencies, {})

    assert result1 is result2
    assert result1["request"] is dependencies["request"]


def test_missing_dependency_skipped(resolver):
    """Missing dependencies should be skipped without error."""
    empty_deps = {}
    result = resolver.resolve(needs_request, empty_deps, {})
    assert "request" not in result


def test_partial_dependencies_available(resolver, mock_request):
    """Only available dependencies should be injected."""
    partial_deps = {"request": mock_request}
    result = resolver.resolve(needs_multiple, partial_deps, {})

    assert "request" in result
    assert "session" not in result
    assert "user" not in result
