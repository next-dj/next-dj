"""Tests for examples/forms greet app: redirects, login, settings, messages."""

import pytest
from django.apps import apps
from django.test import Client

from next.forms import form_action_manager


@pytest.mark.django_db()
def test_home_without_session_redirects_to_login(client: Client) -> None:
    """Test that GET /home/ without session returns 302 to /login/."""
    response = client.get("/home/")
    assert response.status_code == 302
    assert response.url == "/login/"


@pytest.mark.django_db()
def test_login_without_session_returns_200_with_form(client: Client) -> None:
    """Test that GET /login/ without session returns 200 with login form."""
    response = client.get("/login/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Login" in content
    assert "username" in content.lower() or "Username" in content
    assert "password" in content.lower() or "Password" in content
    assert "form" in content.lower()


@pytest.mark.django_db()
def test_login_with_valid_credentials_redirects_to_home(client: Client) -> None:
    """Test that POST login with valid credentials redirects to /home/."""
    login_url = form_action_manager.get_action_url("login")
    response = client.post(
        login_url,
        data={"username": "form", "password": "form"},
        follow=False,
    )
    assert response.status_code == 302
    assert response.url == "/home/"


@pytest.mark.django_db()
def test_after_login_home_shows_greeting(client: Client) -> None:
    """Test that after login GET /home/ returns 200 with greeting."""
    login_url = form_action_manager.get_action_url("login")
    client.post(login_url, data={"username": "form", "password": "form"})
    response = client.get("/home/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Greeting form!" in content


@pytest.mark.django_db()
def test_login_with_session_redirects_to_home(client: Client) -> None:
    """Test that GET /login/ with session redirects to /home/."""
    login_url = form_action_manager.get_action_url("login")
    client.post(login_url, data={"username": "form", "password": "form"})
    response = client.get("/login/")
    assert response.status_code == 302
    assert response.url == "/home/"


@pytest.mark.django_db()
def test_home_with_session_shows_greeting_and_settings_link(client: Client) -> None:
    """Test that GET /home/ with session shows greeting and link to /settings/."""
    login_url = form_action_manager.get_action_url("login")
    client.post(login_url, data={"username": "form", "password": "form"})
    response = client.get("/home/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Greeting form!" in content
    assert "/settings/" in content


@pytest.mark.django_db()
def test_settings_without_session_redirects_to_login(client: Client) -> None:
    """Test that GET /settings/ without session redirects to /login/."""
    response = client.get("/settings/")
    assert response.status_code == 302
    assert response.url == "/login/"


@pytest.mark.django_db()
def test_settings_with_session_returns_200_with_form(client: Client) -> None:
    """Test that GET /settings/ with session returns 200 with settings form."""
    login_url = form_action_manager.get_action_url("login")
    client.post(login_url, data={"username": "form", "password": "form"})
    response = client.get("/settings/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Settings" in content
    assert "username" in content.lower() or "Username" in content
    assert "password" in content.lower() or "Password" in content


@pytest.mark.django_db()
def test_settings_post_redirects_to_home_and_shows_message(client: Client) -> None:
    """Test that POST settings redirects to /home/ and shows updated greeting."""
    login_url = form_action_manager.get_action_url("login")
    client.post(login_url, data={"username": "form", "password": "form"})
    settings_url = form_action_manager.get_action_url("settings")
    response = client.post(
        settings_url,
        data={"username": "alice", "password": "newpass"},
        follow=False,
    )
    assert response.status_code == 302
    assert response.url == "/home/"
    response_home = client.get("/home/")
    assert response_home.status_code == 200
    content = response_home.content.decode()
    assert "Greeting alice!" in content


def test_check_duplicate_url_parameters() -> None:
    """Test check_duplicate_url_parameters check."""
    checks_module = __import__(
        "next.checks", fromlist=["check_duplicate_url_parameters"]
    )
    check_duplicate_url_parameters = checks_module.check_duplicate_url_parameters
    app_configs = apps.get_app_configs()
    errors = check_duplicate_url_parameters(app_configs)
    assert errors == []


def test_check_missing_page_content() -> None:
    """Test check_missing_page_content check."""
    checks_module = __import__("next.checks", fromlist=["check_missing_page_content"])
    check_missing_page_content = checks_module.check_missing_page_content
    app_configs = apps.get_app_configs()
    errors = check_missing_page_content(app_configs)
    assert errors == []


def test_home_page_module_has_context(client: Client) -> None:
    """Test that home page module has get_username context."""
    import greet.pages.home.page as home_page

    assert hasattr(home_page, "get_username")
    assert callable(home_page.get_username)


def test_login_page_module_has_action(client: Client) -> None:
    """Test that login page module has login_handler and LoginForm."""
    import greet.pages.login.page as login_page

    assert hasattr(login_page, "login_handler")
    assert callable(login_page.login_handler)
    assert hasattr(login_page, "LoginForm")


def test_settings_page_module_has_action(client: Client) -> None:
    """Test that settings page module has settings_handler and SettingsForm."""
    import greet.pages.settings.page as settings_page

    assert hasattr(settings_page, "settings_handler")
    assert callable(settings_page.settings_handler)
    assert hasattr(settings_page, "SettingsForm")


@pytest.mark.django_db()
def test_root_redirects_to_home(client: Client) -> None:
    """Test that GET / redirects to /home/."""
    response = client.get("/")
    assert response.status_code == 302
    assert response.url == "/home/"


@pytest.mark.django_db()
def test_login_invalid_credentials_re_renders_form(client: Client) -> None:
    """Test that POST login with invalid credentials re-renders form with errors."""
    login_url = form_action_manager.get_action_url("login")
    response = client.post(
        login_url,
        data={"username": "wrong", "password": "wrong"},
        follow=False,
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "Invalid" in content or "error" in content.lower()


@pytest.mark.django_db()
def test_logout_clears_session_and_redirects_to_login(client: Client) -> None:
    """Test that POST logout clears session and redirects to /login/."""
    login_url = form_action_manager.get_action_url("login")
    client.post(login_url, data={"username": "form", "password": "form"})
    response = client.get("/home/")
    assert response.status_code == 200

    logout_url = form_action_manager.get_action_url("logout")
    response = client.post(logout_url, follow=False)
    assert response.status_code == 302
    assert response.url == "/login/"

    response = client.get("/home/")
    assert response.status_code == 302
    assert response.url == "/login/"


@pytest.mark.django_db()
def test_home_page_shows_logout_button_when_logged_in(client: Client) -> None:
    """Test that /home/ shows logout button when user is logged in."""
    login_url = form_action_manager.get_action_url("login")
    client.post(login_url, data={"username": "form", "password": "form"})
    response = client.get("/home/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Logout" in content


def test_home_page_module_has_logout_action() -> None:
    """Test that home page module has logout_handler action."""
    import greet.pages.home.page as home_page

    assert hasattr(home_page, "logout_handler")
    assert callable(home_page.logout_handler)
