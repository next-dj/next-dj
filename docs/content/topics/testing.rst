.. _topics-testing:

Testing
=======

next.dj ships ``next.testing`` with a test client, registry isolation, signal capture, action helpers, and HTML utilities.
This page covers the public surface of the module and the patterns for testing pages, components, forms, and signals end to end.

.. contents::
   :local:
   :depth: 2

Overview
--------

The module provides nine submodules.

``next.testing.client``.
   ``NextClient`` extends Django's test client and boots the file router.

``next.testing.isolation``.
   ``reset_registries`` clears every framework registry between tests.

``next.testing.actions``.
   Helpers to invoke registered actions without crafting POST bodies.

``next.testing.signals``.
   ``SignalRecorder`` captures payloads inside a context manager.

``next.testing.rendering``.
   Helpers to render a single page or component in isolation.

``next.testing.loaders``.
   Utilities to eager load pages and components before a test.

``next.testing.html``.
   HTML inspection helpers.

``next.testing.patching``.
   Context managers to swap framework parts at runtime.

``next.testing.deps``.
   Builders for ``ResolutionContext`` test doubles.

Boot the Suite
--------------

Add pytest plus pytest-django.

.. code-block:: bash
   :caption: shell

   uv pip install pytest pytest-django

Configure ``DJANGO_SETTINGS_MODULE`` so the framework can load.

.. code-block:: ini
   :caption: pytest.ini

   [pytest]
   DJANGO_SETTINGS_MODULE = config.settings

Isolate Registries
------------------

Add an ``autouse`` fixture in ``conftest.py`` to clear every registry between tests.

.. code-block:: python
   :caption: conftest.py

   import pytest

   from next.testing.isolation import reset_registries


   @pytest.fixture(autouse=True)
   def _next_isolation():
       reset_registries()
       yield
       reset_registries()

The helper clears page, component, action, signal, and static registries.
A test that registers a page or a context function therefore does not pollute the next test.

NextClient
----------

``NextClient`` boots the file router and registers actions on creation.
Use it for end to end HTTP tests.

.. code-block:: python
   :caption: tests/test_index.py

   from next.testing.client import NextClient


   def test_index() -> None:
       response = NextClient().get("/")
       assert response.status_code == 200

The client mirrors Django's ``Client`` API.
``get``, ``post``, ``put``, ``delete``, and ``patch`` all work.
The client automatically follows redirects with ``follow=True``.

Posting to Actions
~~~~~~~~~~~~~~~~~~

Use ``action_url`` to compute the dispatch URL.

.. code-block:: python
   :caption: tests/test_create_action.py

   from next.forms.uid import action_url
   from next.testing.client import NextClient


   def test_create_note(db) -> None:
       url = action_url("create_note")
       response = NextClient().post(url, {"title": "Test", "body": ""})
       assert response.status_code == 302

Render a Page
-------------

Use ``next.testing.rendering`` to render a page without an HTTP round trip.

.. code-block:: python
   :caption: render isolation

   from next.testing.rendering import render_page


   def test_index_body() -> None:
       html = render_page("notes/routes/page.py")
       assert "Notes" in html

The helper invokes context functions and the template loader in the same order as a real request.
Use it for snapshot tests and template assertion tests that do not need URL routing.

Capture Signals
---------------

``SignalRecorder`` subscribes to a signal on enter and unsubscribes on exit.

.. code-block:: python
   :caption: test with recorder

   from next.signals import action_dispatched
   from next.testing.signals import SignalRecorder


   def test_emits(db) -> None:
       with SignalRecorder(action_dispatched) as recorder:
           NextClient().post("/_next/form/abc/", {"title": "hi"})
       assert len(recorder.calls) == 1
       call = recorder.calls[0]
       assert call.kwargs["name"] == "create_note"

The recorder holds a list of ``RecordedCall`` instances with ``args``, ``kwargs``, and ``sender``.

Invoking Actions Directly
-------------------------

``next.testing.actions`` exposes helpers to invoke a handler without HTTP.

.. code-block:: python
   :caption: direct invocation

   from notes.forms import NoteForm
   from next.testing.actions import dispatch_action


   def test_handler_logic(db) -> None:
       response = dispatch_action(
           "create_note",
           form_data={"title": "Direct", "body": ""},
       )
       assert response.status_code == 302

The helper builds the form, runs the validation pipeline, and returns the handler response.
Use it for unit-style tests that do not need to exercise the URL resolver.

HTML Utilities
--------------

``next.testing.html`` provides selectors and assertions for inspecting rendered HTML.

.. code-block:: python
   :caption: html assertions

   from next.testing.html import assert_text, find_one


   def test_index_lists_first_note() -> None:
       response = NextClient().get("/")
       html = response.content.decode()
       title = find_one(html, "h1")
       assert_text(title, "Notes")

The helpers wrap a light HTML parser so tests stay readable without pulling a full DOM library.

Patching
--------

``next.testing.patching`` provides context managers to swap backends, static collectors, and serializers without editing settings.

.. code-block:: python
   :caption: temporary backend

   from next.testing.patching import patch_static_collector


   def test_temporary_backend() -> None:
       with patch_static_collector(MyCollector):
           response = NextClient().get("/")
       assert response.status_code == 200

The patch reverts on exit, so the next test sees the original configuration.

Resolution Context Doubles
--------------------------

``next.testing.deps`` builds fake ``ResolutionContext`` instances for unit tests on providers.

.. code-block:: python
   :caption: provider unit test

   from next.testing.deps import make_context


   def test_provider_handles_int(db) -> None:
       context = make_context(url_kwargs={"id": 7})
       value = my_provider.resolve(make_param("DUrl[int]"), context)
       assert value == 7

Use this for testing custom providers without booting the router.

Common Patterns
---------------

Full End to End Flow
~~~~~~~~~~~~~~~~~~~~

Use ``NextClient`` to walk through a real flow.
Create an instance through a POST to ``action_url``, fetch the redirect target, assert that the new row appears on the listing.

Form Validation Failure
~~~~~~~~~~~~~~~~~~~~~~~

POST to an action URL with invalid data, assert that the response status is ``200`` and that the rendered body contains the expected error.

Signal Emission
~~~~~~~~~~~~~~~

Wrap an action invocation with ``SignalRecorder(action_dispatched)`` and assert that the call list has one entry with the expected payload.

System Checks
~~~~~~~~~~~~~

Pytest can run ``manage.py check`` as part of the suite.

.. code-block:: python
   :caption: check test

   from django.core.management import call_command


   def test_no_check_warnings() -> None:
       call_command("check", verbosity=0)

See Also
--------

.. seealso::

   :doc:`/content/howto/test-a-page-with-actions` for a recipe.
   :doc:`/content/ref/testing` for the public API.
   :doc:`/content/topics/signals` for the signal catalog.
