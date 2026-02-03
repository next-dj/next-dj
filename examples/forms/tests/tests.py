"""Tests for examples/forms todos app: CRUD operations for todos."""

import importlib.util
from pathlib import Path

import pytest
from django.apps import apps
from django.http import HttpRequest
from django.test import Client
from todos.models import Todo
from todos.pages import page as home_page

from next.forms import form_action_manager


@pytest.mark.django_db()
def test_home_page_returns_200(client: Client) -> None:
    """Test that GET / returns 200 with todo list."""
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Todo List" in content
    assert "Create New Todo" in content


@pytest.mark.django_db()
def test_home_shows_empty_message_when_no_todos(client: Client) -> None:
    """Test that home page shows empty message when no todos exist."""
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "No todos yet" in content


@pytest.mark.django_db()
def test_create_todo_creates_new_todo(client: Client) -> None:
    """Test that POST create_todo creates a new todo."""
    create_url = form_action_manager.get_action_url("create_todo")
    response = client.post(
        create_url,
        data={
            "title": "Test Todo",
            "description": "Test description",
            "is_completed": False,
        },
        follow=False,
    )
    assert response.status_code == 302
    assert response.url == "/"

    assert Todo.objects.count() == 1
    todo = Todo.objects.first()
    assert todo.title == "Test Todo"
    assert todo.description == "Test description"
    assert todo.is_completed is False


@pytest.mark.django_db()
def test_home_shows_todos_after_creation(client: Client) -> None:
    """Test that home page shows todos after creation."""
    Todo.objects.create(title="Test Todo", description="Test", is_completed=False)
    response = client.get("/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Test Todo" in content


@pytest.mark.django_db()
def test_edit_page_returns_200(client: Client) -> None:
    """Test that GET /edit/<id>/ returns 200 with edit form."""
    todo = Todo.objects.create(
        title="Test Todo", description="Test", is_completed=False
    )
    response = client.get(f"/edit/{todo.id}/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Edit Todo" in content
    assert "Test Todo" in content


@pytest.mark.django_db()
def test_edit_page_shows_initial_data(client: Client) -> None:
    """Test that edit page shows initial data from todo."""
    todo = Todo.objects.create(
        title="Original Title",
        description="Original Description",
        is_completed=True,
    )
    response = client.get(f"/edit/{todo.id}/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Original Title" in content
    assert "Original Description" in content


@pytest.mark.django_db()
def test_update_todo_updates_existing_todo(client: Client) -> None:
    """Test that POST update_todo updates an existing todo."""
    todo = Todo.objects.create(
        title="Original", description="Original", is_completed=False
    )
    update_url = form_action_manager.get_action_url("update_todo")
    response = client.post(
        update_url,
        data={
            "title": "Updated Title",
            "description": "Updated Description",
            "is_completed": True,
            "_url_param_id": str(todo.id),
        },
        follow=False,
    )
    assert response.status_code == 302
    assert response.url == "/"

    todo.refresh_from_db()
    assert todo.title == "Updated Title"
    assert todo.description == "Updated Description"
    assert todo.is_completed is True


@pytest.mark.django_db()
def test_edit_page_404_for_nonexistent_todo(client: Client) -> None:
    """Test that edit page returns 404 for nonexistent todo."""
    response = client.get("/edit/999/")
    assert response.status_code == 404


@pytest.mark.django_db()
def test_todo_str_method(client: Client) -> None:
    """Test that Todo.__str__ returns title."""
    todo = Todo.objects.create(
        title="Test Todo", description="Test", is_completed=False
    )
    assert str(todo) == "Test Todo"


@pytest.mark.django_db()
def test_get_initial_returns_empty_dict_for_nonexistent_todo(client: Client) -> None:
    """Test that TodoEditForm.get_initial returns empty dict for nonexistent todo."""
    edit_path = (
        Path(__file__).resolve().parent.parent
        / "todos"
        / "pages"
        / "edit"
        / "[int:id]"
        / "page.py"
    )
    spec = importlib.util.spec_from_file_location("edit_page", edit_path)
    edit_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edit_page)

    request = HttpRequest()
    request.method = "GET"

    result = edit_page.TodoEditForm.get_initial(request, id=999)
    assert result == {}


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
    """Test that home page module has get_todos context."""
    assert hasattr(home_page, "get_todos")
    assert callable(home_page.get_todos)


def test_home_page_module_has_action(client: Client) -> None:
    """Test that home page module has create_todo_handler and TodoForm."""
    assert hasattr(home_page, "create_todo_handler")
    assert callable(home_page.create_todo_handler)
    assert hasattr(home_page, "TodoForm")


def test_edit_page_module_has_action(client: Client) -> None:
    """Test that edit page module has update_todo_handler and TodoEditForm."""
    edit_path = (
        Path(__file__).resolve().parent.parent
        / "todos"
        / "pages"
        / "edit"
        / "[int:id]"
        / "page.py"
    )
    spec = importlib.util.spec_from_file_location("edit_page", edit_path)
    edit_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edit_page)

    assert hasattr(edit_page, "update_todo_handler")
    assert callable(edit_page.update_todo_handler)
    assert hasattr(edit_page, "TodoEditForm")
