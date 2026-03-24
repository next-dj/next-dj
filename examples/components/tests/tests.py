from pathlib import Path

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import get_resolver
from myapp.models import Post

from next.components import components_manager, get_component
from next.forms import form_action_manager


User = get_user_model()


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
    """Register, create post, view, edit; form actions work."""
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


def test_post_card_component_resolves_from_home_template_path() -> None:
    """post_card is visible from root page template path."""
    example_root = Path(__file__).resolve().parent.parent
    home_template = example_root / "myapp" / "pages" / "template.djx"
    assert home_template.exists()
    components_manager._ensure_backends()
    card_info = get_component("post_card", home_template.resolve())
    assert card_info is not None


def test_author_chip_component_resolves_from_home_template_path() -> None:
    """author_chip is visible from the same scope as post_card."""
    example_root = Path(__file__).resolve().parent.parent
    home_template = example_root / "myapp" / "pages" / "template.djx"
    assert home_template.exists()
    components_manager._ensure_backends()
    chip = get_component("author_chip", home_template.resolve())
    assert chip is not None
    assert chip.is_simple is False


def test_header_composite_resolves_from_layout_path() -> None:
    """Root composite header is visible from layout.djx."""
    example_root = Path(__file__).resolve().parent.parent
    layout_template = example_root / "myapp" / "pages" / "layout.djx"
    assert layout_template.exists()
    components_manager._ensure_backends()
    header_info = get_component("header", layout_template.resolve())
    assert header_info is not None
    assert header_info.is_simple is False


def test_profile_component_removed() -> None:
    """Old profile component path is gone (example was replaced by blog)."""
    example_root = Path(__file__).resolve().parent.parent
    component_py = (
        example_root / "myapp" / "pages" / "_components" / "profile" / "component.py"
    )
    assert not component_py.exists()
