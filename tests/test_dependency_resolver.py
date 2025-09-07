import types

from next.dependency_resolver import dependency_resolver
from next.pages import ContextManager, Page


# Basic injection
def test_injects_request():
    def f(request):
        return request
    deps = {"request": lambda: "req"}
    wrapped = dependency_resolver(f, deps)
    assert wrapped() == "req"

# Doesn't override explicit kwargs
def test_explicit_overrides():
    def f(request):
        return request
    deps = {"request": lambda: "wrong"}
    wrapped = dependency_resolver(f, deps)
    assert wrapped(request="right") == "right"

# ContextManager integration
def test_collect_context_with_request(tmp_path):
    cm = ContextManager()
    file = tmp_path / "page.py"
    file.write_text("")
    def provide_user(user):
        return {"username": user}
    cm.register_context(file, None, provide_user)
    # Simulate a request object with user and session attributes
    request_obj = type("R", (), {"user": "brad", "session": {}})()
    ctx = cm.collect_context(file, request=request_obj)
    assert ctx["username"] == "brad"

def make_request():
    return types.SimpleNamespace(user="neo", session={})

def test_page_render_injects_request(tmp_path):
    file = tmp_path / "page.py"
    file.write_text("")
    page = Page()
    page.register_template(file, "Hello {{ user }}!")

    # register context function that uses DI
    def context_user(user):
        # Context function for DI test. Returns user for template rendering.
        return {"user": user}
    # Register context for the correct file path
    page._context_manager.register_context(file, None, context_user)

    html = page.render(file, make_request())
    assert "neo" in html
