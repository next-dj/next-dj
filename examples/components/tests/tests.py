import importlib.util
from pathlib import Path
from typing import Literal

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import get_resolver
from myapp.models import Post
from myapp.pages.account.login.page import LoginForm
from myapp.pages.posts.create.page import PostCreateForm

from next.components import get_component, load_component_template, render_component
from next.forms import form_action_manager


User = get_user_model()

example_root = Path(__file__).resolve().parent.parent


@pytest.fixture()
def home_template() -> Path:
    path = example_root / "myapp" / "pages" / "template.djx"
    assert path.exists()
    return path.resolve()


@pytest.fixture()
def layout_template() -> Path:
    path = example_root / "myapp" / "pages" / "layout.djx"
    assert path.exists()
    return path.resolve()


@pytest.fixture()
def post_create_template() -> Path:
    path = example_root / "myapp" / "pages" / "posts" / "create" / "template.djx"
    assert path.exists()
    return path.resolve()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("url", "expected_status"),
    [
        ("/", 200),
        ("/account/login/", 200),
        ("/account/register/", 200),
        ("/posts/99999/details/", 404),
        ("/authors/99999/", 404),
        ("/nope/", 404),
    ],
    ids=["home", "login", "register", "detail_404", "author_404", "unknown"],
)
def test_public_pages_status(client, url: str, expected_status: int) -> None:
    """Public routes return expected HTTP status."""
    response = client.get(url)
    assert response.status_code == expected_status


@pytest.mark.django_db()
def test_home_lists_posts_and_header(client) -> None:
    """Home shows post cards and global header."""
    author = User.objects.create_user(username="writer", password="x")
    Post.objects.create(title="Alpha", content="Body", author=author)
    response = client.get("/")
    assert response.status_code == 200
    text = response.content.decode()
    assert "Articles" in text
    assert "Blog" in text
    assert "Components demo" in text
    assert "Alpha" in text
    assert "writer" in text
    assert "/authors/" in text
    assert "View" in text


@pytest.mark.django_db()
def test_home_pagination_second_page(client) -> None:
    """Paginator shows second page when more than 10 posts."""
    author = User.objects.create_user(username="u", password="pw")
    for i in range(11):
        Post.objects.create(title=f"Post {i}", content="c", author=author)
    response = client.get("/?page=2")
    assert response.status_code == 200
    content = response.content.decode()
    assert "2 / 2" in content or "page=2" in content


@pytest.mark.django_db()
def test_post_detail_and_recommendations(client) -> None:
    """Detail page shows title, body, and up to three recommendations."""
    a = User.objects.create_user(username="a", password="pw")
    for i in range(5):
        Post.objects.create(title=f"T{i}", content=f"C{i}", author=a)
    posts = list(Post.objects.order_by("id"))
    first = posts[0]
    response = client.get(f"/posts/{first.id}/details/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "T0" in content
    assert "C0" in content
    assert "Published" in content
    assert "Recommended" in content
    # three other posts as cards (titles T1..T4 minus current)
    assert content.count("View") >= 3


@pytest.mark.django_db()
def test_anonymous_redirect_from_profile(client) -> None:
    """Profile page requires login."""
    response = client.get("/account/profile/")
    assert response.status_code == 302
    assert "/account/login/" in response.url


@pytest.mark.django_db()
def test_authors_page_shows_posts_and_name(client) -> None:
    """Author page lists posts and display name."""
    author = User.objects.create_user(
        username="pat",
        password="x",
        first_name="Patricia",
        last_name="Writer",
    )
    Post.objects.create(title="By Pat", content="Hi", author=author)
    response = client.get(f"/authors/{author.id}/")
    assert response.status_code == 200
    text = response.content.decode()
    assert "pat" in text
    assert "Patricia" in text
    assert "Writer" in text
    assert "By Pat" in text


@pytest.mark.django_db()
def test_profile_update_changes_names(client) -> None:
    """Profile ModelForm updates User first and last name."""
    user = User.objects.create_user(username="quinn", password="pw")
    assert client.login(username="quinn", password="pw")
    url = form_action_manager.get_action_url("update_profile")
    response = client.post(
        url,
        {
            "first_name": "Quinn",
            "last_name": "Dev",
        },
        follow=True,
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.first_name == "Quinn"
    assert user.last_name == "Dev"


@pytest.mark.django_db()
def test_register_creates_user(client) -> None:
    """Registration creates a user account."""
    reg_url = form_action_manager.get_action_url("register")
    client.post(
        reg_url,
        {"username": "prof", "password1": "good-pass-9x", "password2": "good-pass-9x"},
        follow=True,
    )
    u = User.objects.get(username="prof")
    assert u.check_password("good-pass-9x")


@pytest.mark.django_db()
def test_anonymous_redirect_from_create(client) -> None:
    """Unauthenticated users cannot open create post page."""
    response = client.get("/posts/create/")
    assert response.status_code == 302
    assert "/account/login/" in response.url


@pytest.mark.django_db()
def test_register_login_create_edit_flow(client) -> None:
    """Register, create post, view, and edit. Form actions work."""
    reg_url = form_action_manager.get_action_url("register")
    response = client.post(
        reg_url,
        {
            "username": "bob",
            "password1": "a-secure-pass-1",
            "password2": "a-secure-pass-1",
        },
        follow=True,
    )
    assert response.status_code == 200
    bob = User.objects.get(username="bob")

    create_url = form_action_manager.get_action_url("create_post")
    response = client.post(
        create_url,
        {"title": "Hello", "content": "World content"},
        follow=True,
    )
    assert response.status_code == 200
    post = Post.objects.get(title="Hello")
    assert post.author.username == "bob"

    post_response = client.get(f"/posts/{post.id}/details/")
    assert post_response.status_code == 200
    detail_html = post_response.content.decode()
    assert "Edit" in detail_html
    assert f"/authors/{bob.id}/" in detail_html

    update_url = form_action_manager.get_action_url("update_post")
    response = client.post(
        update_url,
        {
            "_url_param_id": str(post.id),
            "title": "Hello2",
            "content": "World2",
        },
        follow=True,
    )
    assert response.status_code == 200
    post.refresh_from_db()
    assert post.title == "Hello2"


@pytest.mark.django_db()
def test_edit_forbidden_for_non_author(client) -> None:
    """Only the author can open the edit page."""
    alice = User.objects.create_user(username="alice", password="pw")
    bob = User.objects.create_user(username="bob", password="pw")
    post = Post.objects.create(title="Mine", content="x", author=alice)
    client.force_login(bob)
    response = client.get(f"/posts/{post.id}/edit/")
    assert response.status_code == 403


@pytest.mark.django_db()
def test_update_post_forbidden_for_non_author(client) -> None:
    """Non-author cannot submit update_post."""
    alice = User.objects.create_user(username="alice2", password="pw")
    bob = User.objects.create_user(username="bob2", password="pw")
    post = Post.objects.create(title="P", content="c", author=alice)
    client.force_login(bob)
    update_url = form_action_manager.get_action_url("update_post")
    response = client.post(
        update_url,
        {
            "_url_param_id": str(post.id),
            "title": "Hacked",
            "content": "No",
        },
    )
    assert response.status_code == 403


@pytest.mark.django_db()
def test_authenticated_header_shows_create(client) -> None:
    """Logged-in user sees Create in header."""
    User.objects.create_user(username="carol", password="pw")
    assert client.login(username="carol", password="pw")
    response = client.get("/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "Profile" in body
    assert "Create" in body


@pytest.mark.django_db()
def test_authenticated_home_logout_form_includes_csrf(client) -> None:
    """Logout POST must send csrfmiddlewaretoken (component context binds csrf_token)."""
    User.objects.create_user(username="csrfuser", password="pw")
    assert client.login(username="csrfuser", password="pw")
    response = client.get("/")
    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="csrfmiddlewaretoken"' in body
    assert 'action="/account/logout/"' in body


@pytest.mark.django_db()
def test_logout_post(client) -> None:
    """Logout clears session."""
    user = User.objects.create_user(username="dave", password="pw")
    client.force_login(user)
    response = client.post("/account/logout/", follow=False)
    assert response.status_code in (200, 302)


@pytest.mark.django_db()
def test_login_form_action(client) -> None:
    """Login form authenticates user."""
    User.objects.create_user(username="ernie", password="secret12345")
    login_url = form_action_manager.get_action_url("login")
    response = client.post(
        login_url,
        {"username": "ernie", "password": "secret12345"},
        follow=False,
    )
    assert response.status_code == 302


@pytest.mark.django_db()
def test_login_invalid_password_returns_layout(client) -> None:
    """Invalid login re-renders full page (layout + components), not a bare fragment."""
    User.objects.create_user(username="eve", password="correct")
    assert client.get("/account/login/").status_code == 200
    login_action = form_action_manager.get_action_url("login")
    response = client.post(
        login_action,
        {"username": "eve", "password": "wrong"},
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "Invalid username or password." in body
    assert "<title>Blog</title>" in body
    assert "Sign in" in body


def test_myapp_app_config() -> None:
    """Myapp is registered."""
    app_config = apps.get_app_config("myapp")
    assert app_config is not None
    assert app_config.name == "myapp"


def test_config_urls_loaded() -> None:
    """URLconf is loadable."""
    resolver = get_resolver()
    assert resolver.url_patterns is not None


@pytest.mark.django_db()
def test_create_post_page_shows_branch_scoped_banner(client) -> None:
    """draft_banner from pages/posts/_components appears on the create form page."""
    User.objects.create_user(username="banner_user", password="pw")
    assert client.login(username="banner_user", password="pw")
    response = client.get("/posts/create/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "pages/posts/_components" in body


@pytest.mark.django_db()
def test_post_model_str() -> None:
    """Post __str__ returns title."""
    author = User.objects.create_user(username="test", password="pw")
    post = Post.objects.create(title="Test Post", content="Content", author=author)
    assert str(post) == "Test Post"


@pytest.mark.django_db()
def test_post_get_absolute_url() -> None:
    """Post get_absolute_url returns detail page URL."""
    author = User.objects.create_user(username="test", password="pw")
    post = Post.objects.create(title="Test", content="C", author=author)
    url = post.get_absolute_url()
    assert f"/posts/{post.id}/details/" in url


@pytest.mark.django_db()
def test_home_invalid_page_number(client) -> None:
    """Invalid page number defaults to page 1."""
    author = User.objects.create_user(username="u", password="pw")
    Post.objects.create(title="P1", content="c", author=author)
    response = client.get("/?page=invalid")
    assert response.status_code == 200


@pytest.mark.django_db()
def test_login_form_get_initial(client) -> None:
    """LoginForm get_initial returns empty dict."""
    request = RequestFactory().get("/")
    initial = LoginForm.get_initial(request)
    assert initial == {}


@pytest.mark.django_db()
def test_login_form_clean_missing_credentials() -> None:
    """LoginForm clean handles missing username or password."""
    form = LoginForm(data={"username": "", "password": ""})
    assert not form.is_valid()


@pytest.mark.django_db()
def test_login_handler_next_url_validation(client) -> None:
    """Login handler validates next URL to prevent open redirect."""
    User.objects.create_user(username="test", password="pw")
    login_url = form_action_manager.get_action_url("login")
    response = client.post(
        login_url,
        {
            "username": "test",
            "password": "pw",
            "next": "https://evil.com",
        },
        follow=False,
    )
    assert response.status_code == 302
    assert response.url == "/"


@pytest.mark.django_db()
def test_register_form_password_mismatch(client) -> None:
    """Register with mismatched passwords fails."""
    reg_url = form_action_manager.get_action_url("register")
    response = client.post(
        reg_url,
        {
            "username": "test",
            "password1": "pass123",
            "password2": "different",
        },
    )
    assert response.status_code == 200
    assert not User.objects.filter(username="test").exists()


@pytest.mark.django_db()
def test_profile_form_get_initial(client) -> None:
    """Profile form get_initial returns user first and last name."""
    user = User.objects.create_user(
        username="test",
        password="pw",
        first_name="Test",
        last_name="User",
    )
    client.force_login(user)
    response = client.get("/account/profile/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Test" in content
    assert "User" in content


@pytest.mark.django_db()
def test_create_post_form_get_initial() -> None:
    """Create post form get_initial returns empty dict."""
    request = RequestFactory().get("/")
    initial = PostCreateForm.get_initial(request)
    assert initial == {}


@pytest.mark.django_db()
def test_edit_post_form_get_initial(client) -> None:
    """Edit post form get_initial returns post title and content."""
    author = User.objects.create_user(username="test", password="pw")
    post = Post.objects.create(title="Original", content="Content", author=author)
    client.force_login(author)
    response = client.get(f"/posts/{post.id}/edit/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Original" in content
    assert "Content" in content


@pytest.mark.django_db()
def test_profile_update_anonymous_redirects(client) -> None:
    """Anonymous user cannot update profile."""
    update_url = form_action_manager.get_action_url("update_profile")
    response = client.post(
        update_url,
        {"first_name": "Test", "last_name": "User"},
        follow=False,
    )
    assert response.status_code == 302
    assert "/account/login/" in response.url


@pytest.mark.django_db()
def test_register_form_username_exists(client) -> None:
    """Register with existing username fails."""
    User.objects.create_user(username="existing", password="pw")
    reg_url = form_action_manager.get_action_url("register")
    response = client.post(
        reg_url,
        {
            "username": "existing",
            "password1": "newpass123",
            "password2": "newpass123",
        },
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "already exists" in content


@pytest.mark.django_db()
def test_edit_post_form_get_initial_missing_post() -> None:
    """Edit form get_initial returns empty dict for missing post."""
    edit_page_path = (
        example_root / "myapp" / "pages" / "posts" / "[int:id]" / "edit" / "page.py"
    )

    spec = importlib.util.spec_from_file_location("edit_page", edit_page_path)
    if spec and spec.loader:
        edit_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(edit_module)
        initial = edit_module.PostEditForm.get_initial(99999)
        assert initial == {}


@pytest.mark.django_db()
def test_update_post_handler_non_author_forbidden(client) -> None:
    """Update post handler returns 403 for non-author."""
    alice = User.objects.create_user(username="alice", password="pw")
    bob = User.objects.create_user(username="bob", password="pw")
    post = Post.objects.create(title="Alice Post", content="Content", author=alice)
    client.force_login(bob)
    update_url = form_action_manager.get_action_url("update_post")
    response = client.post(
        update_url,
        {
            "_url_param_id": str(post.id),
            "title": "Hacked",
            "content": "No",
        },
    )
    assert response.status_code == 403


@pytest.mark.django_db()
def test_create_post_anonymous_redirects(client) -> None:
    """Anonymous user cannot create post."""
    create_url = form_action_manager.get_action_url("create_post")
    response = client.post(
        create_url,
        {"title": "Test", "content": "Content"},
        follow=False,
    )
    assert response.status_code == 302
    assert "/account/login/" in response.url


@pytest.mark.parametrize(
    ("template_path_fixture", "component_name", "visibility", "simple_kind"),
    [
        pytest.param(
            "home_template", "post_card", "found", "any", id="post_card_from_home"
        ),
        pytest.param(
            "home_template",
            "author_chip",
            "found",
            "composite",
            id="author_chip_from_home",
        ),
        pytest.param(
            "layout_template", "header", "found", "composite", id="header_from_layout"
        ),
        pytest.param(
            "home_template",
            "draft_banner",
            "missing",
            "n/a",
            id="draft_banner_hidden_from_home",
        ),
        pytest.param(
            "post_create_template",
            "draft_banner",
            "found",
            "simple",
            id="draft_banner_from_post_create",
        ),
    ],
)
def test_get_component_resolves_by_template_scope(
    template_path_fixture: str,
    component_name: str,
    visibility: Literal["found", "missing"],
    simple_kind: Literal["any", "simple", "composite", "n/a"],
    request: pytest.FixtureRequest,
) -> None:
    """Component lookup follows template path scope (root vs branch _components)."""
    template_path: Path = request.getfixturevalue(template_path_fixture)
    info = get_component(component_name, template_path)
    if visibility == "missing":
        assert info is None
        return
    assert info is not None
    if simple_kind == "simple":
        assert info.is_simple is True
    elif simple_kind == "composite":
        assert info.is_simple is False


def test_render_component_server_time_uses_render_callable(
    layout_template: Path,
) -> None:
    """server_time is a composite that implements ``render()`` in component.py."""
    info = get_component("server_time", layout_template)
    assert info is not None
    request = RequestFactory().get("/")
    html = render_component(info, {}, request=request)
    assert "UTC" in html
    assert "Rendered by" in html


def test_load_component_template_version_stamp(layout_template: Path) -> None:
    """version_stamp uses an inline component template string (no component.djx)."""
    info = get_component("version_stamp", layout_template)
    assert info is not None
    source = load_component_template(info)
    assert source is not None
    assert "next-dj" in source


def test_example_includes_expected_component_artifacts() -> None:
    """Stable disk layout for documented demos (simple, composite, branch scope)."""
    assert (
        example_root / "myapp" / "pages" / "_components" / "post_card" / "component.djx"
    ).exists()
    assert (
        example_root / "myapp" / "pages" / "posts" / "_components" / "draft_banner.djx"
    ).exists()
    assert (example_root / "root_components" / "header" / "component.py").exists()
    assert (example_root / "root_components" / "server_time" / "component.py").exists()
    assert (
        example_root / "root_components" / "version_stamp" / "component.py"
    ).exists()
    assert not (
        example_root / "root_components" / "version_stamp" / "component.djx"
    ).exists()
