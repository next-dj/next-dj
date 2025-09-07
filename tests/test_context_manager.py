import types

from next.pages import ContextManager


def make_request():
    return types.SimpleNamespace(user="brad", session={"x": 1})

def test_collect_context_injects_request(tmp_path):
    cm = ContextManager()
    f = tmp_path / "page.py"

    def provide_user(request):
        return {"username": request.user}
    cm.register_context(f, None, provide_user)

    ctx = cm.collect_context(f, request=make_request())
    assert ctx["username"] == "brad"

def test_collect_context_keyed(tmp_path):
    cm = ContextManager()
    f = tmp_path / "page.py"

    def provide_session(session):
        return session
    cm.register_context(f, "sess", provide_session)

    ctx = cm.collect_context(f, request=make_request())
    assert ctx["sess"] == {"x": 1}
