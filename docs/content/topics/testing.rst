.. _topics-testing:

Testing
=======

next.dj ships ``next.testing`` with a test client, registry isolation, signal capture, action helpers, and HTML utilities.
This page covers the public surface of the module and the patterns for testing pages, components, forms, and signals end to end.
No helper in ``next.testing`` imports pytest, so every one of them works under Django's ``TestCase``, stdlib ``unittest``, and pytest alike.

.. contents::
   :local:
   :depth: 2

Choose the right helper
-----------------------

``next.testing`` groups its helpers into focused submodules.
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
   * - Resolve an action name to its dispatch URL without posting
     - ``NextClient.get_action_url``
     - ``next.testing`` or ``next.testing.client``
   * - GET a URL as a partial zone request
     - ``NextClient.get_zones``
     - ``next.testing`` or ``next.testing.client``
   * - Decode a partial response for structural assertions
     - ``envelope_of``
     - ``next.testing`` or ``next.testing.client``
   * - Inspect ops, targets, and assets of a patch envelope
     - ``PartialEnvelope``
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
   * - Read the collector a patched block built
     - ``StaticCollectorProxy``
     - ``next.testing`` or ``next.testing.patching``
   * - Unit-test a custom provider or resolver path
     - ``resolve_call``, ``make_resolution_context``
     - ``next.testing`` or ``next.testing.deps``
   * - Force-import pages or components in tests
     - ``eager_load_components``, ``eager_load_pages``, ``clear_loaded_dirs``
     - ``next.testing`` or ``next.testing.loaders``
   * - Reload backends after mutating settings or registries
     - ``reset_registries`` (opt-in), or narrower ``reset_components`` / ``reset_form_actions``
     - ``next.testing`` or ``next.testing.isolation``
   * - Drop the page template cache after rewriting template files on disk
     - ``reset_page_cache``
     - ``next.testing`` or ``next.testing.isolation``
   * - Clear the form registries, diagnostics, and wizard backend
     - ``reset_form_registration_state``
     - ``next.testing`` or ``next.testing.isolation``

Every helper in the table is importable from the ``next.testing`` package or from its submodule.
See :doc:`/content/ref/testing` for generated signatures.

Boot the suite
--------------

The ``next.testing`` helpers assume the app registry is populated before any helper is imported.

Pytest.
   Set ``DJANGO_SETTINGS_MODULE`` in ``pytest.ini`` so ``pytest-django`` can configure Django before collecting tests.
   Run the suite with ``uv run pytest``.

   .. code-block:: ini
      :caption: pytest.ini

      [pytest]
      DJANGO_SETTINGS_MODULE = config.settings
      python_files = test_*.py

Stdlib ``unittest``.
   Call ``django.setup()`` once before importing any ``next.testing`` helper, then run the suite with the standard runner.
   The helpers carry no pytest fixtures, so a plain ``TestCase`` drives them through ``setUp`` and ``addCleanup``.

   .. code-block:: python
      :caption: tests/test_signals_unittest.py

      from pathlib import Path
      from unittest import TestCase

      from next.signals import page_rendered
      from next.testing import NextClient, SignalRecorder, clear_loaded_dirs, eager_load_pages

      PROJECT_ROOT = Path(__file__).resolve().parent.parent

      class IndexTest(TestCase):
          def setUp(self) -> None:
              clear_loaded_dirs()
              self.addCleanup(clear_loaded_dirs)
              eager_load_pages(PROJECT_ROOT / "notes" / "pages")

          def test_index_emits_page_rendered(self) -> None:
              with SignalRecorder(page_rendered) as recorder:
                  NextClient().get("/")
              assert len(recorder.events) == 1

Registry state between tests
----------------------------

Action and component registrations are side effects of importing ``page.py`` and ``component.py`` modules.
The canonical scaffold imports them once per session with the eager loaders from ``next.testing.loaders`` and leaves the registries alone between tests.

.. code-block:: python
   :caption: conftest.py

   from pathlib import Path

   import pytest
   from next.testing import eager_load_pages

   PROJECT_ROOT = Path(__file__).resolve().parent

   @pytest.fixture(autouse=True, scope="session")
   def _load_pages() -> None:
       eager_load_pages(PROJECT_ROOT / "notes" / "pages")

The session fixture runs the ``@context`` and ``@action`` decorators before the first test dispatches a request.
Every project under ``examples/`` uses this scaffold.

.. note::

   When ``LAZY_COMPONENT_MODULES = True`` in ``NEXT_FRAMEWORK``, bulk import of ``component.py`` modules from configured component roots is skipped during ``AppConfig.ready``.
   Call ``eager_load_components()`` from ``next.testing.loaders`` once per session to import every registered ``component.py`` regardless of the flag.

   .. code-block:: python
      :caption: conftest.py, eager loading with lazy modules

      import pytest
      from next.testing.loaders import eager_load_components

      @pytest.fixture(autouse=True, scope="session")
      def _load_components() -> None:
          eager_load_components()

   With the default ``LAZY_COMPONENT_MODULES = False``, the configured component roots are imported during ``AppConfig.ready``.
   Components that live inside a page tree register during the URL router walk instead, so a suite that renders them without any HTTP request triggers the walk first, for example by reversing one route with ``page_reverse()``.
   See :doc:`/content/howto/test-a-component-in-isolation` for the full recipe.
   See :ref:`ref-settings` for the full description of ``LAZY_COMPONENT_MODULES``.

Resetting registries
~~~~~~~~~~~~~~~~~~~~

``reset_registries()`` is an opt-in helper for tests that mutate ``NEXT_FRAMEWORK`` or the registries themselves.
It reloads the form-action and component backends from the current settings.
Two narrower helpers reset a single registry.

- ``reset_components()`` reloads only the component backends, through ``ComponentsManager.reload``.
- ``reset_form_actions()`` reloads only the form-action backends, through ``FormActionManager.reload``.

.. warning::

   A reset does not bring import-time registrations back.
   Python does not re-import a module that is already in ``sys.modules``, so ``@action`` handlers and page-tree components registered by earlier imports stay absent after the reset.
   Do not call ``reset_registries()`` from an autouse fixture in an ordinary suite.
   Reserve it for tests that verify registry behaviour itself.

A third helper, ``reset_page_cache()``, resets no registry.
It calls ``Page.clear_template_caches`` to drop the page template cache and is useful when a test rewrites template files on disk.

For tests that probe registration itself, ``reset_form_registration_state()`` clears every form registry, the registration diagnostics buffer, and resets the wizard backend in one call.

Tests that write ``template.djx`` or ``page.py`` files to ``tmp_path`` register fresh state on every run, so a suite dedicated to them pairs a registry reset with a page-cache reset.

.. code-block:: python
   :caption: conftest.py for a tmp_path suite

   import pytest
   from next.testing.isolation import reset_page_cache, reset_registries

   @pytest.fixture(autouse=True)
   def _isolation():
       reset_registries()
       yield
       reset_registries()
       reset_page_cache()

Eager page loading
~~~~~~~~~~~~~~~~~~

``eager_load_pages(base_dir)`` imports every ``page.py`` under a given directory.
It returns the list of imported ``page.py`` paths and raises ``FileNotFoundError`` when the directory does not exist.
A repeated call for the same directory returns an empty list, because the loader memoises per absolute directory.
Use it when a test suite does not go through the full request cycle and must trigger ``@context`` and ``@action`` side-effects manually.
``clear_loaded_dirs()`` drops the per-directory memoisation so a later ``eager_load_pages`` call re-imports.
It is intended for self-tests of the loader and for the rare case of rewritten ``page.py`` files within one session.
A normal test suite does not call it, since each pytest session starts a fresh interpreter.

NextClient
----------

``NextClient`` is a thin subclass of Django's ``Client`` that adds ``post_action``, ``get_action_url``, and ``get_zones``.
``post_action`` and ``get_action_url`` resolve an action name through ``resolve_action_url`` before delegating to the underlying client.

.. code-block:: python
   :caption: tests/test_index.py

   from next.testing.client import NextClient

   def test_index() -> None:
       response = NextClient().get("/")
       assert response.status_code == 200

Every inherited method behaves as documented for :class:`django.test.Client`, including ``follow=True``.

Posting to actions
~~~~~~~~~~~~~~~~~~

``NextClient.post_action`` resolves an action name to its URL and posts the data in one call.

.. code-block:: python
   :caption: tests/test_create_action.py

   from next.testing.client import NextClient

   def test_create_note(db) -> None:
       response = NextClient().post_action("create_note", {"title": "Test", "body": ""})
       assert response.status_code == 302

The ``origin`` keyword fills the hidden ``_next_form_origin`` field the ``{% form %}`` tag emits in the browser, the page URL the dispatcher resolves to re-render on a validation failure.

.. code-block:: python
   :caption: failing submission re-renders the origin

   def test_blank_title_rerenders(db) -> None:
       response = NextClient().post_action("create_note", {"title": ""}, origin="/")
       assert response.status_code == 200

A value already present in ``data`` under ``_next_form_origin`` wins over the keyword, so protocol-level tests can drive the raw field directly, including posting without it to assert the HTTP 400 rejection.

``NextClient.get_action_url`` returns the dispatch URL without posting, for tests that need the URL itself.
Both methods resolve the name through ``resolve_action_url`` from ``next.testing.actions``.
An unknown name raises ``FormActionNotFoundError`` from ``next.forms``.

Partial requests
~~~~~~~~~~~~~~~~

The client stamps the partial switch, the zone list, and the client asset version for you.
Pass ``partial=True`` and ``zones=`` to ``post_action`` to turn the POST into a patch request scoped to a zone.
``zones`` accepts one name or a tuple of names.

.. code-block:: python
   :caption: tests/test_partial_action.py

   from next.testing.client import NextClient, envelope_of

   def test_partial_morph(db) -> None:
       response = NextClient().post_action(
           "create_note",
           {"title": "hi"},
           partial=True,
           zones="notes",
       )
       envelope = envelope_of(response)
       assert "notes" in envelope.zone_targets()

``NextClient.get_zones(url, zones)`` GETs a URL as a partial request for the named zones.
``zones`` is one name or a tuple of names.
Both ``post_action`` and ``get_zones`` accept a ``version=`` keyword that stamps the ``X-Next-Version`` header so tests can drive the version-sync branch.

.. code-block:: python
   :caption: tests/test_partial_get.py

   from next.testing.client import NextClient, envelope_of

   def test_zone_get() -> None:
       response = NextClient().get_zones("/notes/", "notes")
       envelope = envelope_of(response)
       assert envelope.zone_targets() == ["notes"]

Both methods forward any extra keyword argument to the underlying request as a WSGI META key, so the remaining protocol headers reach the server unchanged.
Pass ``HTTP_X_NEXT_VALIDATE="title"`` to drive the validate-only branch, ``HTTP_X_NEXT_MERGE="append"`` to drive a paginating merge, and ``HTTP_X_NEXT_ORIGIN`` to name the host page.

``envelope_of(response)`` decodes a patch response into a ``PartialEnvelope``.
It raises ``AssertionError`` when the response is not a patch envelope, so a navigation fallback never passes a structural assertion.
``PartialEnvelope`` exposes ``version``, ``ops``, and ``assets``, plus ``op_verbs``, ``targets``, ``zone_targets``, ``form_targets``, ``form_meta``, ``toasts``, and ``html_for_zone`` for asserting on the server contract without parsing HTML.
``html_for_zone`` raises ``AssertionError`` when no op targets the named zone.
See :doc:`/content/topics/partial-rendering/index` for the zone and patch model these helpers exercise.

Render a page
-------------

Use ``next.testing.rendering`` to render a page without an HTTP round trip.

.. code-block:: python
   :caption: render isolation

   from next.testing.rendering import render_page

   def test_index_body() -> None:
       html = render_page("notes/pages/page.py")
       assert "Notes" in html

``render_page`` reads the static body source, the ``template`` attribute or a registered file template such as ``template.djx``, then runs context functions and the static collector.
It does not invoke a ``render()`` function declared in ``page.py``.
Use ``NextClient`` for pages whose body is built by ``render()``.
Use it for snapshot tests and template assertion tests that do not need URL routing.

Pass an ``HttpRequest`` as the second positional argument to supply a custom request.
When omitted the helper synthesises one through ``RequestFactory().get("/")`` so context functions and the static collector see a real request object.
Extra keyword arguments are forwarded to the underlying ``page.render`` call as URL kwargs, which feeds them into ``DUrl`` markers and other URL-scoped providers.

.. code-block:: python
   :caption: render with a custom request

   from django.test import RequestFactory
   from next.testing.rendering import render_page

   def test_index_with_request() -> None:
       request = RequestFactory().get("/?debug=1")
       html = render_page("notes/pages/page.py", request)
       assert "Notes" in html

Capture signals
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
``SignalRecorder`` accepts one or more signals and raises ``ValueError`` when constructed with none.
It exposes these public members.

``events``.
   The full list of captured ``SignalEvent`` instances in emission order.

``start()``.
   Connects receivers for every tracked signal and returns the recorder.
   Called automatically on context entry.

``stop()``.
   Disconnects receivers for every tracked signal.
   Called automatically on context exit.

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

Action helpers
--------------

``next.testing.actions`` exposes ``resolve_action_url`` and ``build_form_for``.
``resolve_action_url`` turns an action name into its dispatch URL.
``build_form_for`` builds a bound form for an action so a unit test can assert validation without HTTP.
Both raise ``FormActionNotFoundError`` from ``next.forms`` for an unknown action name, with the closest registered names rendered into the message.
``build_form_for`` raises ``LookupError`` for an action registered without a form class.

.. code-block:: python
   :caption: tests/test_action_helpers.py

   from next.testing.actions import build_form_for, resolve_action_url

   def test_form_validates(db) -> None:
       url = resolve_action_url("create_note")
       form = build_form_for("create_note", {"title": "Direct", "body": ""})
       assert form.is_valid()

HTML utilities
--------------

``next.testing.html`` provides assertions for inspecting rendered HTML fragments.
In the second example below, ``at`` is the template path the component is referenced from, and it drives which components are visible.

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
           at="notes/pages/template.djx",
           props={"note": {"title": "First"}},
       )
       assert_has_class(html, "note-card")

See :doc:`/content/howto/test-a-component-in-isolation` for the full component recipe.
``find_anchor`` returns the matching anchor tag and raises ``LookupError`` when no anchor matches the filters, see :func:`next.testing.html.find_anchor` for the accepted keywords.
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
     - Handle exposing the collector most recently built inside the patch.

A ``StaticCollectorProxy`` is yielded by ``patch_static_collector(capture=True)``.
Its ``.collector`` attribute holds the collector most recently built inside the block, so a page that renders twice leaves the second one, and a test can assert on the emitted styles and scripts without parsing HTML.
Pass ``factory=`` to swap the collector implementation entirely.
The callable runs in place of the default ``create_collector`` and returns a custom ``StaticCollector`` for the duration of the block.

Use ``patch_static_collector(capture=True)`` to inspect which assets a page emits.

.. code-block:: python
   :caption: tests/test_static_capture.py

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

   from next.testing.client import NextClient
   from next.testing.patching import override_next_settings

   def test_with_strict_context() -> None:
       with override_next_settings(STRICT_CONTEXT=True):
           response = NextClient().get("/")
       assert response.status_code == 200

The patch reverts on exit, so the next test sees the original configuration.
The merge is shallow.
A key supplied as a keyword replaces the whole value under that name, so overriding one entry of a nested mapping drops its siblings for the block.
The helper wraps Django's ``override_settings``, so the ``settings_reloaded`` chain fires and the framework managers rebuild against the patched configuration inside the block.

``override_dependency`` binds a stub value to a ``Depends("name")`` registration for the block, and restores the previous registration on exit.

.. code-block:: python
   :caption: stubbing a named dependency

   from next.testing.client import NextClient
   from next.testing.patching import override_dependency

   def test_stubbed_theme() -> None:
       with override_dependency("layout_theme", {"name": "Stub"}):
           response = NextClient().get("/")
       assert b"Stub" in response.content

``override_provider`` prepends a provider instance to the resolver's provider list for the block.
The prepended provider wins over every auto-registered provider that would otherwise claim the same parameter.
Implement the ``ParameterProvider`` protocol on a plain class for the stub, because subclassing ``RegisteredParameterProvider`` registers the provider globally.

.. code-block:: python
   :caption: prepending a stub provider

   from next.testing.deps import resolve_call
   from next.testing.patching import override_provider

   class EveryIntIsSeven:
       def can_handle(self, param, context) -> bool:
           return param.annotation is int

       def resolve(self, param, context) -> int:
           return 7

   def count_notes(limit: int) -> int:
       return limit

   def test_provider_wins() -> None:
       with override_provider(EveryIntIsSeven()):
           kwargs = resolve_call(count_notes)
       assert kwargs == {"limit": 7}

``override_form_action`` registers a handler under an action name for the block.
It snapshots the whole action registry on entry and restores it on exit, so an action the project already registered under that name survives the block.
The override claims the name binding, which means it wins name lookup even when such an action exists.
Pass ``form_class=`` to give the override a form, which is what ``build_form_for`` and a bound dispatch need.

.. code-block:: python
   :caption: temporary form action

   from django import forms
   from next.testing.client import NextClient
   from next.testing.patching import override_form_action

   class TitleForm(forms.Form):
       title = forms.CharField()

   def test_stub_action_receives_post() -> None:
       seen: list[str] = []

       def handler(form: TitleForm) -> None:
           seen.append(form.cleaned_data["title"])

       with override_form_action("create_note", handler, form_class=TitleForm):
           NextClient().post_action("create_note", {"title": "Stub"}, origin="/")
       assert seen == ["Stub"]

``override_component_backends`` takes backend config dicts positionally and replaces ``COMPONENT_BACKENDS`` for the block.
It reads the manager's backends on entry, so the swap takes effect immediately rather than on the next render.
The example below points a temporary root at ``tmp_path``, whose ``_components/info_card/`` folder holds the component under test.

.. code-block:: python
   :caption: temporary component root

   from next.testing.patching import override_component_backends
   from next.testing.rendering import render_component_by_name

   def test_component_from_temporary_root(tmp_path) -> None:
       config = {
           "BACKEND": "next.components.FileComponentsBackend",
           "DIRS": [str(tmp_path)],
           "COMPONENTS_DIR": "_components",
       }
       with override_component_backends(config):
           html = render_component_by_name("info_card", at=tmp_path / "template.djx")
       assert "info-card" in html

Resolution context doubles
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

System checks
-------------

Pytest can run ``manage.py check`` as part of the suite.

.. code-block:: python
   :caption: check test

   from django.core.management import call_command

   def test_no_check_warnings() -> None:
       call_command("check", verbosity=0)

See also
--------

.. seealso::

   :doc:`/content/howto/test-a-page-with-actions` for a full end to end flow, a form validation failure, and a signal emission assertion with working code.
   :doc:`/content/ref/testing` for the public API.
   :doc:`/content/topics/signals` for the signal catalog.
