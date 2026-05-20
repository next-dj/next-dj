.. _topics-testing:

Testing
=======

next.dj ships ``next.testing`` with a test client, registry isolation, signal capture, action helpers, and HTML utilities.
This page covers the public surface of the module and the patterns for testing pages, components, forms, and signals end to end.

.. contents::
   :local:
   :depth: 2

Choose the Right Helper
-----------------------

``next.testing`` groups its helpers into focused submodules covering the client, isolation, signal capture, rendering, loaders, HTML assertions, patching, action helpers, and dependency context builders.
The table below maps each testing goal to the helper and its import path.

.. list-table::
   :header-rows: 1
   :widths: 35 35 30

   * - Goal
     - Use
     - Import
   * - HTTP request to a page or action
     - ``NextClient``
     - ``next.testing`` or ``next.testing.client``
   * - POST to a registered action by name
     - ``NextClient.post_action``
     - ``next.testing`` or ``next.testing.client``
   * - Render a page body without HTTP
     - ``render_page``
     - ``next.testing`` or ``next.testing.rendering``
   * - Render a component in isolation
     - ``render_component_by_name``
     - ``next.testing`` or ``next.testing.rendering``
   * - Assert on rendered HTML structure
     - ``find_anchor``, ``assert_has_class``, ``assert_missing_class``
     - ``next.testing`` or ``next.testing.html``
   * - Capture one or more signals explicitly
     - ``SignalRecorder`` or ``capture_signals``
     - ``next.testing`` or ``next.testing.signals``
   * - Capture every framework signal at once
     - ``capture_framework_signals``
     - ``next.testing`` or ``next.testing.signals``
   * - Inspect a captured signal payload
     - ``SignalEvent``
     - ``next.testing`` or ``next.testing.signals``
   * - Validate a form without HTTP
     - ``build_form_for``, ``resolve_action_url``
     - ``next.testing`` or ``next.testing.actions``
   * - Temporarily override ``NEXT_FRAMEWORK`` or framework wiring
     - ``override_next_settings``, ``override_dependency``, ``override_provider``, ``override_form_action``, ``override_component_backends``, ``patch_static_collector``
     - ``next.testing`` or ``next.testing.patching``
   * - Wrap the static collector for assertions
     - ``StaticCollectorProxy``
     - ``next.testing`` or ``next.testing.patching``
   * - Unit-test a custom provider or resolver path
     - ``resolve_call``, ``make_resolution_context``
     - ``next.testing`` or ``next.testing.deps``
   * - Force-import pages or components in tests
     - ``eager_load_components``, ``eager_load_pages``, ``clear_loaded_dirs``
     - ``next.testing`` or ``next.testing.loaders``
   * - Clear registries between tests
     - ``reset_registries`` (call from an autouse fixture), or narrower ``reset_components`` / ``reset_form_actions`` / ``reset_page_cache``
     - ``next.testing`` or ``next.testing.isolation``

You can import everything above from the ``next.testing`` package. Submodule imports stay valid when you prefer explicit paths.
See :doc:`/content/ref/testing` for generated signatures.

Boot the Suite
--------------

Set ``DJANGO_SETTINGS_MODULE`` in ``pytest.ini`` so pytest-django can configure Django before collecting tests.
Run the suite with ``uv run pytest``.
The ``next.testing`` helpers assume the app registry is populated; ``pytest-django`` does this automatically.
A stdlib ``unittest`` suite calls ``django.setup()`` once before importing any ``next.testing`` helper.

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
Two narrower helpers reset a single registry.

- ``reset_components()`` reloads only the component backends.
- ``reset_form_actions()`` reloads only the form-action backends.

A third helper, ``reset_page_cache()``, resets no registry.
It drops the page template cache and is useful when a test rewrites template files on disk.

Tests that write ``template.djx`` or ``page.py`` files to ``tmp_path`` need both helpers:

.. code-block:: python
   :caption: conftest.py

   import pytest
   from next.testing.isolation import reset_page_cache, reset_registries

   @pytest.fixture(autouse=True)
   def _isolation():
       reset_registries()
       yield
       reset_registries()
       reset_page_cache()

.. note::

   When ``LAZY_COMPONENT_MODULES = True`` in ``NEXT_FRAMEWORK``, bulk import of ``component.py`` modules from configured component roots is skipped during ``AppConfig.ready``.
   After ``reset_registries()``, decorator side effects from those modules are absent until resolve time unless you call ``eager_load_components()`` from ``next.testing.loaders``, which imports every registered ``component.py`` regardless of the flag.

   .. code-block:: python
      :caption: conftest.py, eager loading with lazy modules

      import pytest
      from next.testing.isolation import reset_registries
      from next.testing.loaders import eager_load_components

      @pytest.fixture(autouse=True)
      def _next_isolation():
          reset_registries()
          eager_load_components()
          yield
          reset_registries()

   With the default ``LAZY_COMPONENT_MODULES = False``, all registrations are in place after ``AppConfig.ready``, so the extra call is unnecessary.

   ``eager_load_pages(base_dir)`` is a separate helper that imports every ``page.py`` under a given directory.
   Use it when a test suite does not go through the full request cycle and must trigger ``@context`` and ``@action`` side-effects manually.
   ``clear_loaded_dirs()`` drops the per-directory memoisation so a later ``eager_load_pages`` call re-imports.
   It is needed only when a test rewrites ``page.py`` files on disk within a single session.
   See :ref:`ref-settings` for the full description of ``LAZY_COMPONENT_MODULES``.

NextClient
----------

``NextClient`` is a thin subclass of Django's ``Client`` that adds ``post_action`` and ``get_action_url``, both of which resolve an action name through ``resolve_action_url``.
It does nothing special on creation.
The file router builds lazily through Django's URL resolver on the first request.
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
Both methods resolve the name through ``resolve_action_url`` from ``next.testing.actions``.

Render a Page
-------------

Use ``next.testing.rendering`` to render a page without an HTTP round trip.

.. code-block:: python
   :caption: render isolation

   from next.testing.rendering import render_page

   def test_index_body() -> None:
       html = render_page("notes/pages/page.py")
       assert "Notes" in html

``render_page`` reads the static body source, the ``template`` attribute or a ``template.djx`` file, then runs context functions and the static collector.
It does not invoke a ``render()`` function declared in ``page.py``.
Use ``NextClient`` for pages whose body is built by ``render()``.
Use it for snapshot tests and template assertion tests that do not need URL routing.

Capture Signals
---------------

``SignalRecorder`` subscribes to one or more signals on enter and unsubscribes on exit.

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
``SignalRecorder`` accepts one or more signals and exposes these public members.

``events``.
   The full list of captured ``SignalEvent`` instances in emission order.

``start()``.
   Connects receivers for every tracked signal and returns the recorder. Called automatically on context entry.

``stop()``.
   Disconnects receivers for every tracked signal. Called automatically on context exit.

``events_for(signal)``.
   Returns the list of captured events emitted by that signal.

``first_for(signal)``.
   Returns the first captured event for that signal, or raises ``LookupError`` when none was captured.

``last_for(signal)``.
   Returns the last captured event for that signal, or raises ``LookupError`` when none was captured.

``clear()``.
   Drops every captured event without disconnecting.

The recorder is also iterable and supports ``len()`` over the captured events.

Two convenience wrappers cover the common multi-signal cases.

``capture_signals(*signals)`` returns a started ``SignalRecorder`` and reads well in ``with`` statements.

.. code-block:: python
   :caption: test with capture_signals

   from next.signals import action_dispatched, page_rendered
   from next.testing.client import NextClient
   from next.testing.signals import capture_signals

   def test_dispatch_and_render(db) -> None:
       with capture_signals(action_dispatched, page_rendered) as recorder:
           NextClient().post_action("create_note", {"title": "hi"})
       assert len(recorder.events_for(action_dispatched)) == 1
       dispatch = recorder.first_for(action_dispatched)
       assert dispatch.kwargs["action_name"] == "create_note"

``capture_framework_signals()`` attaches to every name in ``next.signals.__all__``, which helps integration tests assert ordering without listing signals by hand.

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

   from next.testing.client import NextClient
   from next.testing.html import assert_has_class, find_anchor
   from next.testing.rendering import render_component_by_name

   def test_index_links_to_note() -> None:
       html = NextClient().get("/").content.decode()
       anchor = find_anchor(html, text="First")
       assert "First" in anchor

   def test_card_class() -> None:
       html = render_component_by_name(
           "note_card",
           at="notes/pages/page.py",
           context={"note": {"title": "First"}},
       )
       assert_has_class(html, "note-card")

``find_anchor`` returns the matching anchor tag.
It accepts an ``href`` keyword that matches the anchor ``href`` exactly and a ``text`` keyword that matches a substring against the anchor's stripped inner text.
It raises ``LookupError`` when no anchor matches the filters.
``assert_has_class`` and ``assert_missing_class`` check the class list of the first start tag in the fragment.

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
     - Temporarily swap the static collector implementation.
   * - ``StaticCollectorProxy``
     - Thin proxy around a collector for introspection in tests.

A ``StaticCollectorProxy`` is yielded by ``patch_static_collector(capture=True)``.
Its ``.collector`` attribute holds the collector built inside the block, so a test can assert on the emitted styles and scripts without parsing HTML.

Use ``patch_static_collector(capture=True)`` to inspect which assets a page emits:

.. code-block:: python

   from next.testing.patching import patch_static_collector
   from next.testing.client import NextClient

   def test_collects_styles() -> None:
       with patch_static_collector(capture=True) as proxy:
           NextClient().get("/")
       assert proxy.collector is not None
       styles = proxy.collector.assets_in_slot("styles")
       assert len(styles) > 0

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

``next.testing.deps.make_resolution_context`` builds a ``ResolutionContext`` for unit tests on providers.
``next.testing.deps.resolve_call`` resolves a callable's dependencies and returns the kwargs mapping.
Both accept the same loose keyword arguments, ``request``, ``form``, ``url_kwargs``, and ``context_data``.

.. code-block:: python
   :caption: provider unit test

   from next.testing.deps import make_resolution_context

   def test_context_carries_url_kwargs() -> None:
       context = make_resolution_context(url_kwargs={"id": 7})
       assert context.url_kwargs["id"] == 7

Pass ``resolve_call`` a callable whose annotated parameters a provider can fill, then assert on the returned mapping.
Use these helpers for testing custom providers without booting the router.

Common Patterns
---------------

.. seealso::

   :doc:`/content/howto/test-a-page-with-actions` walks a full end to end flow, a form validation failure, and a signal emission assertion with working code.

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
