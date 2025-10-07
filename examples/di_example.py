"""
dependency injection example for next.dj framework.

demonstrates automatic injection of request, session, and user objects
into context and render functions based on type hints and parameter names.
shows both keyed context functions and typed render functions.
"""

from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest

from next.dependencies import DependencyResolver


def get_user_context(request: HttpRequest, user) -> dict:
    """
    context function that receives request and user via dependency injection.

    returns dictionary with user id and authentication status. the resolver
    automatically injects request by type hint and user by parameter name.
    """
    user_id = getattr(user, "id", None) if user else None
    is_authenticated = getattr(user, "is_authenticated", False)

    return {
        "user_id": user_id,
        "is_authenticated": is_authenticated,
        "request_method": request.method,
    }


def get_session_info(session: SessionBase) -> str:
    """
    context function that receives session via type hint injection.

    returns session key as string value. demonstrates single-value context
    function that would be registered with a key.
    """
    return session.session_key or "no-session"


def render_greeting(request: HttpRequest, user) -> str:
    """
    render function that uses dependency injection for request and user.

    returns formatted greeting string. demonstrates how render functions
    can receive dependencies without manual wiring.
    """
    username = getattr(user, "username", "Guest") if user else "Guest"
    path = getattr(request, "path", "/")

    return f"Hello {username}! You accessed {path} via {request.method}."


def main():
    """demonstrate dependency resolver with mock dependencies."""
    from unittest.mock import MagicMock

    # create mock dependencies
    mock_request = MagicMock(spec=HttpRequest)
    mock_request.method = "GET"
    mock_request.path = "/example"

    mock_session = MagicMock(spec=SessionBase)
    mock_session.session_key = "abc123xyz"

    mock_user = MagicMock()
    mock_user.id = 42
    mock_user.username = "alice"
    mock_user.is_authenticated = True

    # build dependencies dict
    deps = {
        "request": mock_request,
        "session": mock_session,
        "user": mock_user,
    }

    # create resolver and inject dependencies
    resolver = DependencyResolver()

    # example 1: context function with dict return
    context_kwargs = resolver.resolve(get_user_context, deps, {})
    context_result = get_user_context(**context_kwargs)
    print("context result:", context_result)

    # example 2: keyed context function with single value
    session_kwargs = resolver.resolve(get_session_info, deps, {})
    session_result = get_session_info(**session_kwargs)
    print("session key:", session_result)

    # example 3: render function with typed parameters
    render_kwargs = resolver.resolve(render_greeting, deps, {})
    render_result = render_greeting(**render_kwargs)
    print("render output:", render_result)

    # example 4: explicit kwargs override injected values
    custom_user = MagicMock()
    custom_user.username = "bob"
    custom_user.is_authenticated = True

    explicit_kwargs = {"user": custom_user}
    override_kwargs = resolver.resolve(render_greeting, deps, explicit_kwargs)
    override_result = render_greeting(**override_kwargs)
    print("with override:", override_result)


if __name__ == "__main__":
    main()
