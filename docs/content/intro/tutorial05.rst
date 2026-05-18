.. _intro-tutorial05:

Testing and Autoreload
======================

Goal
----

This final part covers the development workflow.
You install pytest, write end-to-end tests against the Notes application with ``NextClient``, capture signals with ``SignalRecorder``, and learn how the autoreloader picks up file router changes without a server restart.

Prerequisites
-------------

You have finished :doc:`tutorial04`.
The application creates, edits, and deletes notes through registered actions.
The patterns below mirror :doc:`/content/topics/testing`. Keep that page open if you want the full helper catalog.

Walkthrough
-----------

Install Test Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~

The framework ships its own ``next.testing`` module of framework-agnostic helpers.
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
The conftest isolates the next.dj registries between test modules.

.. code-block:: python
   :caption: conftest.py

   import pytest

   from next.testing.isolation import reset_registries


   @pytest.fixture(autouse=True)
   def _next_dj_isolation():
       reset_registries()
       yield
       reset_registries()

``reset_registries()`` reloads the form-action and component backends from the current settings so that a test which swaps ``NEXT_FRAMEWORK`` does not bleed into the next one.
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
The router itself is built lazily through Django's URL resolver, the same as in production.
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
       response = client.post_action("create_note", {"title": "From test", "body": "body"})
       assert response.status_code == 302
       assert Note.objects.filter(title="From test").exists()
       assert response["Location"] == reverse("next:page_")

``post_action`` looks the action name up through ``resolve_action_url`` and posts the data to the dispatch endpoint.
The redirect target matches ``next:page_`` because the handler returns ``HttpResponseRedirect(reverse("next:page_"))``.

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
           NextClient().post_action("create_note", {"title": "Signal", "body": ""})

       assert len(recorder.events) == 1
       event = recorder.events[0]
       assert event.kwargs["action_name"] == "create_note"
       assert event.kwargs["form"].cleaned_data["title"] == "Signal"

``next.signals`` re-exports every framework signal under one import path.
The owning submodule path ``next.forms.signals`` also works if you prefer it.
``SignalRecorder`` is a context manager that subscribes to the signal on entry and unsubscribes on exit.
Each captured event is a ``SignalEvent`` with ``signal``, ``sender``, and ``kwargs`` attributes.

Test Validation Failure
~~~~~~~~~~~~~~~~~~~~~~~

A failed validation does not produce a redirect.
The pipeline re-renders the origin page with the bound form and a non-zero error count.

.. code-block:: python
   :caption: tests/test_notes_actions.py

   def test_create_with_blank_title_rerenders(db) -> None:
       client = NextClient()
       response = client.post_action("create_note", {"title": "", "body": "x"})
       assert response.status_code == 200
       assert b"This field is required" in response.content

The response status is ``200`` because the index page rendered.
This time the failing form replaces the unbound one in the template context.

Use the Autoreloader
~~~~~~~~~~~~~~~~~~~~

The development server already reloads on Python file changes.
The file router has its own reloader for new pages and components.
Run the server and create a new page in a separate terminal.

.. code-block:: bash
   :caption: shell, terminal 1

   uv run python manage.py runserver

.. code-block:: bash
   :caption: shell, terminal 2

   mkdir -p notes/routes/about
   cat > notes/routes/about/page.py <<'PY'
   from next.pages import context


   @context("body")
   def about_body() -> str:
       return "Hello from a new page."
   PY
   cat > notes/routes/about/template.djx <<'JINJA'
   <p>{{ body }}</p>
   JINJA

Within a second the server picks up the change.
Open ``http://127.0.0.1:8000/about/`` and confirm that the new page is served without a manual restart.

.. note::

   The autoreloader emits a ``router_reloaded`` signal each time the route set changes.
   Long-running processes such as websocket subscribers listen for that signal to refresh cached references.

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

Test sees stale routes from another test module.
   Ensure that ``reset_registries()`` runs in an ``autouse`` fixture.
   Without it a page registered in one test stays in the registry for the next test.

``post_action`` raises an unknown action error.
   Make sure the action module is imported before the test runs.
   Tests in ``tests/`` next to ``notes/`` import ``notes`` because pytest places the project root on ``sys.path``.

Autoreloader does not pick up a change.
   Confirm that the changed file lives under one of the configured page roots.
   Files outside ``notes/routes/`` do not trigger a router reload.

Next Steps
----------

The tutorial is complete.

.. seealso::

   :doc:`whatsnext` lists where to go next, by topic.
   :doc:`/content/topics/testing` covers the full testing surface.
   :doc:`/content/internals/autoreload` explains how the reloader watches the filesystem.
