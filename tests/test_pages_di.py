import textwrap
import types

from next.pages import ContextManager, DjxTemplateLoader, Page, PythonTemplateLoader


def _make_request(user="neo", session=None):
    return types.SimpleNamespace(user=user, session=session or {})

def test_context_manager_keyed_and_unkeyed(tmp_path):
    """
    Covers ContextManager.register_context + collect_context()
    with DI of request/session/user and both keyed/unkeyed modes.
    """
    cm = ContextManager()
    page_file = tmp_path / "page.py"
    page_file.write_text("# dummy")
    def unkeyed(user, session):
        return {"u": user, "s": session}
    def keyed(request):
        return request.user
    cm.register_context(page_file, None, unkeyed)
    cm.register_context(page_file, "who", keyed)
    ctx = cm.collect_context(page_file, request=_make_request("brad", {"x": 1}))
    assert ctx["u"] == "brad"
    assert ctx["s"] == {"x": 1}
    assert ctx["who"] == "brad"

def test_page_render_merges_kwargs_and_context(tmp_path):
    """
    Covers Page.render() path: template lookup, context collect, kwargs override, Django Template render.
    """
    p = Page()
    page_file = tmp_path / "page.py"
    page_file.write_text("# dummy")
    p.register_template(page_file, "hello {{ name }} {{ extra }}")
    @p.context
    def ctx_defaults():
        return {"name": "ctx", "extra": "!"}
    html = p.render(page_file, _make_request(), name="kw", extra="!!")
    assert "hello kw !!" in html

def test_python_template_loader_can_load_and_load(tmp_path):
    """
    Covers PythonTemplateLoader.can_load/load_template paths.
    """
    mod = tmp_path / "page.py"
    mod.write_text(textwrap.dedent('''
        # module-level template for PythonTemplateLoader
        template = "User: {{ user }}"
    '''))
    loader = PythonTemplateLoader()
    assert loader.can_load(mod) is True
    tpl = loader.load_template(mod)
    assert isinstance(tpl, str) and "User:" in tpl

def test_djx_template_loader_can_load_and_load(tmp_path):
    """
    Covers DjxTemplateLoader.can_load/load_template paths.
    """
    d = tmp_path / "sub"
    d.mkdir()
    page_file = d / "page.py"
    page_file.write_text("# dummy")
    (d / "template.djx").write_text("Hi {{ user }}")
    loader = DjxTemplateLoader()
    assert loader.can_load(page_file) is True
    tpl = loader.load_template(page_file)
    assert tpl.strip().startswith("Hi")

def test_create_url_pattern_uses_loader_and_view_renders(tmp_path):
    """
    Covers create_url_pattern() loader branch and inner view() -> Page.render() -> HttpResponse.
    Registers a context function for the file so DI injects user into the template.
    """
    d = tmp_path / "my"
    d.mkdir()
    page_file = d / "page.py"
    page_file.write_text("# dummy")
    (d / "template.djx").write_text("Hello {{ user }}!")
    class FakeParser:
        def parse_url_pattern(self, url_path: str):
            return "my/", {}
        def prepare_url_name(self, url_path: str):
            return "my"
    p = Page()
    # Register context for DI so user is injected
    def ctx_user(user):
        return {"user": user}
    p._context_manager.register_context(page_file, None, ctx_user)
    pat = p.create_url_pattern("/my", page_file, FakeParser())
    assert pat is not None
    resp = pat.callback(_make_request("trinity"))
    assert b"Hello trinity!" in resp.content

def test_create_url_pattern_fallbacks_to_module_render(tmp_path):
    """
    Covers the fallback branch where module defines a callable render().
    """
    d = tmp_path / "m"
    d.mkdir()
    page_file = d / "page.py"
    page_file.write_text(textwrap.dedent('''
        from django.http import HttpResponse
        def render(request, **kwargs):
            who = getattr(request, "user", "anon")
            return HttpResponse(f"OK:{who}:{kwargs.get('foo')}")
    '''))
    class FakeParser:
        def parse_url_pattern(self, url_path: str):
            return "m/", {"foo": "bar"}
        def prepare_url_name(self, url_path: str):
            return "m"
    p = Page()
    pat = p.create_url_pattern("/m", page_file, FakeParser())
    assert pat is not None
    resp = pat.callback(_make_request("morpheus"))
    assert b"OK:morpheus:bar" in resp.content

def test_context_decorator_registers_both_branches(tmp_path, monkeypatch):
    """
    Covers @context and @context('key') decorator branches without depending on real frames.
    """
    p = Page()
    page_file = tmp_path / "page.py"
    page_file.write_text("# dummy")
    monkeypatch.setattr(p, "_get_caller_path", lambda bc=1: page_file)
    @p.context
    def unkeyed(user):
        return {"u": user}
    @p.context("who")
    def keyed(request):
        return request.user
    p.register_template(page_file, "U={{ u }} W={{ who }}")
    html = p.render(page_file, _make_request("smith"))
    assert "U=smith" in html and "W=smith" in html
