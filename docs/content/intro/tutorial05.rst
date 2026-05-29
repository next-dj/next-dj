.. _intro-tutorial05:

Testing and Autoreload
======================

Goal
----

This final part covers the development workflow.
You install pytest and write end-to-end tests against the Notes application with ``NextClient`` and ``SignalRecorder``.
You also learn how the autoreloader picks up file router changes without a server restart.

Prerequisites
-------------

You have finished :doc:`tutorial04`.
The application creates, edits, and deletes notes through registered actions.
The patterns below mirror :doc:`/content/topics/testing`. Keep that page open if you want the full helper catalog.

Walkthrough
-----------

Install Test Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~

next.dj ships the ``next.testing`` package, and its helpers work with Django ``TestCase``, stdlib ``unittest``, and pytest alike.
This tutorial drives it with pytest and pytest-django, which you install separately.

.. code-block:: bash
   :caption: shell

   uv add --dev pytest pytest-django

Add the pytest configuration.

.. code-block:: ini
   :caption: pytest.ini

   [pytest]
   DJANGO_SETTINGS_MODULE = config.settings
   python_files = tests.py test_*.py *_tests.py
   addopts = --tb=short

Add a ``conftest.py`` at the project root.
The conftest imports every ``page.py`` so actions register before the tests run.
The ``PAGES_DIR`` path must match the actual app and page-root names, so a project that did not name its app ``notes`` adjusts the path accordingly.

.. code-block:: python
   :caption: conftest.py

   from pathlib import Path
   import pytest
   from next.testing.isolation import reset_registries
   from next.testing.loaders import eager_load_pages

   PAGES_DIR = Path(__file__).resolve().parent / "notes" / "pages"

   @pytest.fixture(autouse=True)
   def _next_dj_isolation():
       reset_registries()
       eager_load_pages(PAGES_DIR)
       yield
       reset_registries()

``reset_registries()`` runs first to create fresh form-action and component backends, so every ``@action`` and ``@component.context`` registration that follows lands on a clean slate.
``eager_load_pages`` then walks ``notes/pages`` and imports every ``page.py``, which runs the decorators and registers them on those fresh backends.
The teardown ``reset_registries()`` clears all state before the next test runs.
Database access uses the standard ``db`` fixture from pytest-django, no extra fixture is needed.

Write the First End-to-End Test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create ``tests/test_notes_e2e.py``.

.. code-block:: python
   :caption: tests/test_notes_e2e.py

   import pytest
   from notes.models import Note
   from next.testing.client import NextClient

   @pytest.fixture
   def client() -> NextClient:
       return NextClient()

   def test_index_lists_notes(client, db) -> None:
       Note.objects.create(title="First", body="hello")
       response = client.get("/")
       assert response.status_code == 200
       assert "First" in response.content.decode()

   def test_detail_renders_note(client, db) -> None:
       note = Note.objects.create(title="Second", body="world")
       response = client.get(f"/notes/{note.id}/")
       assert response.status_code == 200
       assert "Second" in response.content.decode()

``NextClient`` extends Django's test client with two shortcuts for form actions.
``post_action`` resolves an action name to its URL and POSTs in one call.
``get_action_url`` returns that URL without dispatching.
The router itself is built lazily through Django's URL resolver, exactly as in production.
Use the same ``client.get`` and ``client.post`` calls you already know.

Run the tests.

.. code-block:: bash
   :caption: shell

   uv run pytest

Test the Create Action
~~~~~~~~~~~~~~~~~~~~~~

The framework gives each action a stable URL.

.. code-block:: python
   :caption: tests/test_notes_actions.py

   from django.urls import reverse
   from notes.models import Note
   from next.testing.client import NextClient

   def test_create_note_action(db) -> None:
       client = NextClient()
       response = client.post_action("create_note_form", {"title": "From test", "body": "body"})
       assert response.status_code == 302
       assert Note.objects.filter(title="From test").exists()
       assert response["Location"] == reverse("next:page_")

``post_action`` looks the action name up through ``resolve_action_url`` and posts the data to the dispatch endpoint.
The action name ``create_note_form`` is derived automatically from the class name ``CreateNoteForm``.
The redirect target matches ``next:page_`` because ``on_valid`` calls ``redirect_to_origin``.

Capture Action Signals
~~~~~~~~~~~~~~~~~~~~~~

Every dispatch fires the ``action_dispatched`` signal.
``SignalRecorder`` collects events so the test can assert what happened.

.. code-block:: python
   :caption: tests/test_notes_signals.py

   from next.signals import action_dispatched
   from next.testing.client import NextClient
   from next.testing.signals import SignalRecorder

   def test_create_emits_action_dispatched(db) -> None:
       with SignalRecorder(action_dispatched) as recorder:
           NextClient().post_action("create_note_form", {"title": "Signal", "body": ""})

       assert len(recorder) == 1
       event = recorder.first_for(action_dispatched)
       assert event.kwargs["action_name"] == "create_note_form"
       assert event.kwargs["form"].cleaned_data["title"] == "Signal"

``action_dispatched`` is re-exported from ``next.signals`` and also lives on its owning module ``next.forms.signals``.
``SignalRecorder`` is a context manager that subscribes to the signal on entry and unsubscribes on exit.
It accepts several signals at once and exposes ``first_for``, ``last_for``, and ``events_for`` to query the captured events per signal.
Each captured event is a ``SignalEvent`` with ``signal``, ``sender``, and ``kwargs`` attributes.
The ``action_dispatched`` payload carries ``action_name``, ``form``, ``url_kwargs``, ``duration_ms``, ``response_status``, and ``dep_cache``.

Test Validation Failure
~~~~~~~~~~~~~~~~~~~~~~~

A failed validation does not produce a redirect.
The pipeline re-renders the origin page with the bound form and a non-zero error count.

.. code-block:: python
   :caption: tests/test_notes_actions.py

   def test_create_with_blank_title_rerenders(db) -> None:
       client = NextClient()
       response = client.post_action("create_note_form", {"title": "", "body": "x"})
       assert response.status_code == 200
       assert b"This field is required" in response.content

The response status is ``200`` because the index page rendered.
This time the failing form replaces the unbound one in the template context.

Use the Autoreloader
~~~~~~~~~~~~~~~~~~~~

The development server already reloads on Python file changes.
The file router has its own reloader for new pages.
Run the server and create a new page in a separate terminal.

.. code-block:: bash
   :caption: shell, terminal 1

   uv run python manage.py runserver

.. code-block:: bash
   :caption: shell, terminal 2

   mkdir -p notes/pages/about

Add the two page files.

.. code-block:: python
   :caption: notes/pages/about/page.py

   from next.pages import context

   @context("body")
   def about_body() -> str:
       return "Hello from a new page."

.. code-block:: jinja
   :caption: notes/pages/about/template.djx

   <p>{{ body }}</p>

Within a second the server picks up the change.
Open ``http://127.0.0.1:8000/about/`` and confirm that the new page is served without a manual restart.

.. note::

   The framework emits ``action_dispatched`` after a successful handler run, recorded above through ``SignalRecorder``.
   The autoreloader emits a companion ``router_reloaded`` signal each time the route set changes, so a test that wants to react to filesystem-driven route changes can subscribe to it the same way.
   See :doc:`/content/howto/observe-framework-signals` for the full subscriber pattern.

Checkpoint
----------

The project now has tests.

.. code-block:: text
   :caption: tests layout

   tests/
     test_notes_e2e.py
     test_notes_actions.py
     test_notes_signals.py
   conftest.py
   pytest.ini

The full test suite covers the index, the detail page, the create action, the create validation failure, and the ``action_dispatched`` signal.
The development server reloads page and component changes without a manual restart.

Common Pitfalls
---------------

``post_action`` raises an unknown action error.
   A form class registers only when its module is imported.
   Call ``eager_load_pages`` in the test setup so every form and handler registers before the first dispatch.
   For forms in ``forms.py``, also import that module explicitly or rely on ``autodiscover_forms()``.

Tests that rewrite page files on disk see stale handlers.
   ``eager_load_pages`` memoises each directory it has already imported.
   Call ``clear_loaded_dirs()`` from ``next.testing.loaders`` to drop that memo when a test edits ``page.py`` files between runs.

Autoreloader does not pick up a change.
   Confirm that the changed file lives under one of the watched roots.
   The reloader watches ``page.py`` files under the page roots and ``component.py`` files under the component roots, so files elsewhere do not trigger a router reload.

Next Steps
----------

The tutorial is complete.

.. seealso::

   :doc:`whatsnext` lists where to go next, by topic.
   :doc:`/content/topics/testing` covers the full testing surface.
   :doc:`/content/internals/autoreload` explains how the reloader watches the filesystem.
   :doc:`/content/deployment/index` covers production setup once the application is feature complete.
