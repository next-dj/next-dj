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
   ``reset_registries`` reloads the form-action and component backends between tests.

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

The helper reloads the form-action and component backends from the current settings.
``reset_page_cache`` is a separate helper that drops the page template cache when a test rewrites template files on disk.

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
Pass ``follow=True`` to a request to follow redirects, exactly as with Django's ``Client``.

Posting to Actions
~~~~~~~~~~~~~~~~~~

``NextClient.post_action`` resolves an action name to its URL and posts the data in one call.

.. code-block:: python
   :caption: tests/test_create_action.py

   from next.testing.client import NextClient


   def test_create_note(db) -> None:
       response = NextClient().post_action("create_note", {"title": "Test", "body": ""})
       assert response.status_code == 302

``NextClient.get_action_url`` returns the dispatch URL without posting, for tests that need the URL itself.
Both methods resolve the name through ``next.testing.resolve_action_url``.

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
   from next.testing.client import NextClient
   from next.testing.signals import SignalRecorder


   def test_emits(db) -> None:
       with SignalRecorder(action_dispatched) as recorder:
           NextClient().post_action("create_note", {"title": "hi"})
       assert len(recorder.events) == 1
       event = recorder.events[0]
       assert event.kwargs["action_name"] == "create_note"

The recorder holds a list of ``SignalEvent`` instances with ``signal``, ``sender``, and ``kwargs`` attributes.

Action Helpers
--------------

``next.testing.actions`` exposes ``resolve_action_url`` and ``build_form_for``.
``resolve_action_url`` turns an action name into its dispatch URL.
``build_form_for`` builds a bound form for an action so a unit test can assert validation without HTTP.

.. code-block:: python
   :caption: tests/test_action_helpers.py

   from next.testing.actions import build_form_for, resolve_action_url


   def test_form_validates(db) -> None:
       url = resolve_action_url("create_note")
       form = build_form_for("create_note", {"title": "Direct", "body": ""})
       assert form.is_valid()

HTML Utilities
--------------

``next.testing.html`` provides assertions for inspecting rendered HTML fragments.

.. code-block:: python
   :caption: html assertions

   from next.testing.html import assert_has_class, find_anchor
   from next.testing.rendering import render_component_by_name


   def test_index_links_to_note() -> None:
       html = NextClient().get("/").content.decode()
       anchor = find_anchor(html, text="First")
       assert "First" in anchor


   def test_card_class() -> None:
       html = render_component_by_name(
           "note_card",
           at="notes/routes/page.py",
           context={"note": note},
       )
       assert_has_class(html, "note-card")

``find_anchor`` returns the matching anchor tag.
``assert_has_class`` and ``assert_missing_class`` check the class list of the first tag in a fragment.

Patching
--------

``next.testing.patching`` provides context managers that swap framework parts for the duration of a block.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Helper
     - Effect
   * - ``override_next_settings``
     - Temporarily override ``NEXT_FRAMEWORK`` keys.
   * - ``override_dependency``
     - Temporarily replace a named dependency value.
   * - ``override_provider``
     - Temporarily register a parameter provider.
   * - ``override_form_action``
     - Temporarily register a form action.
   * - ``override_component_backends``
     - Temporarily swap the component backend configs.
   * - ``patch_static_collector``
     - Temporarily swap the static collector.

.. code-block:: python
   :caption: temporary settings

   from next.testing.patching import override_next_settings


   def test_with_strict_context() -> None:
       with override_next_settings(STRICT_CONTEXT=True):
           response = NextClient().get("/")
       assert response.status_code == 200

The patch reverts on exit, so the next test sees the original configuration.

Resolution Context Doubles
--------------------------

``next.testing.make_resolution_context`` builds a ``ResolutionContext`` for unit tests on providers.
``next.testing.resolve_call`` resolves a callable's dependencies and returns the kwargs mapping.
Both accept the same loose keyword arguments, ``request``, ``form``, ``url_kwargs``, and ``context_data``.

.. code-block:: python
   :caption: provider unit test

   from next.testing import make_resolution_context, resolve_call


   def test_provider_handles_int(db) -> None:
       context = make_resolution_context(url_kwargs={"id": 7})
       assert context.url_kwargs["id"] == 7
       kwargs = resolve_call(my_view, url_kwargs={"id": 7})
       assert "note" in kwargs

Use this for testing custom providers without booting the router.

Common Patterns
---------------

Full End to End Flow
~~~~~~~~~~~~~~~~~~~~

Use ``NextClient`` to walk through a real flow.
Create an instance through ``post_action``, fetch the redirect target, assert that the new row appears on the listing.

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
