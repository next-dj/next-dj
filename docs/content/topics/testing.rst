.. _topics-testing:

Testing
=======

next.dj ships ``next.testing`` with a test client, registry isolation, signal capture, action helpers, and HTML utilities.
This page covers the public surface of the module and the patterns for testing pages, components, forms, and signals end to end.
Nothing re-exported from ``next.testing`` imports pytest, so every helper works under Django's ``TestCase``, stdlib ``unittest``, and pytest alike.
Pytest lives alone in the opt-in ``next.testing.plugin`` module, which a project loads from its ini file when it wants the fixtures described below.

.. contents::
   :local:
   :depth: 2

Choose the right helper
-----------------------

``next.testing`` groups its helpers into focused submodules, one per testing job.
Every helper is importable from the ``next.testing`` package as well as from its own submodule, except the plugin options and fixtures, which pytest supplies once the plugin is loaded.

.. list-table::
   :header-rows: 1
   :widths: 15 45 40

   * - Submodule
     - Helpers
     - Use it for
   * - ``client``
     - ``NextClient``, ``envelope_of``, ``PartialEnvelope``
     - HTTP against a page or an action, partial zone requests, and structural assertions on a patch envelope.
   * - ``rendering``
     - ``render_page``, ``render_component_by_name``
     - Rendering a page body or a single component without an HTTP round trip.
   * - ``html``
     - ``find_anchor``, ``find_form``, ``form_action``, ``form_fields``, ``hidden_fields``, ``init_payload``, ``assert_has_class``, ``assert_missing_class``
     - Asserting on rendered markup, on a form's target and fields, and on the bootstrap payload.
   * - ``capture``
     - ``SignalRecorder``, ``capture_signals``, ``capture_framework_signals``, ``SignalEvent``
     - Recording signal emissions and reading their payloads.
   * - ``actions``
     - ``build_form_for``, ``resolve_action_url``
     - Validating a form or resolving a dispatch URL without posting.
   * - ``patching``
     - ``override_next_settings``, ``override_dependency``, ``override_provider``, ``override_form_action``, ``override_component_backends``, ``patch_static_collector``, ``StaticCollectorProxy``
     - Swapping settings, a dependency, a provider, an action, the component backends, or the static collector for one block.
   * - ``deps``
     - ``resolve_call``, ``make_resolution_context``
     - Unit-testing a custom provider or resolver path.
   * - ``loaders``
     - ``eager_load_pages``, ``eager_load_components``, ``clear_loaded_dirs``
     - Force-importing pages and components in a suite that skips the request cycle.
   * - ``isolation``
     - ``reset_registries``, ``reset_components``, ``reset_form_actions``, ``reset_form_registration_state``, ``reset_page_cache``, ``reset_component_templates``
     - Reloading backends and dropping caches after mutating settings, registries, or files on disk.
   * - ``plugin``
     - ``next_pages``, ``next_components``, ``next_clear_cache``, ``next_client``
     - The pytest ini options and the client fixture, once the plugin is loaded.

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

   Add ``-p next.testing.plugin`` to ``addopts`` on top of that to get the page loader, the cache isolation, and the ``next_client`` fixture, as `Pytest plugin`_ describes.

Stdlib ``unittest``.
   Call ``django.setup()`` once, then ``django.test.utils.setup_test_environment()``, before importing any ``next.testing`` helper, or run the suite through ``uv run python manage.py test`` so Django's own runner does both.
   The second call is what adds ``testserver`` to ``ALLOWED_HOSTS``, and without it the test client gets a ``DisallowedHost`` response instead of the page.
   The helpers re-exported from ``next.testing`` carry no pytest fixtures, so a ``django.test.TestCase`` drives them directly and the page import happens once in ``setUpModule``.

   .. code-block:: python
      :caption: tests/test_signals_unittest.py

      from pathlib import Path

      from django.test import TestCase

      from next.signals import page_rendered
      from next.testing import NextClient, SignalRecorder, eager_load_pages

      PROJECT_ROOT = Path(__file__).resolve().parent.parent

      def setUpModule() -> None:
          eager_load_pages(PROJECT_ROOT / "notes" / "pages")

      class IndexTest(TestCase):
          def test_index_emits_page_rendered(self) -> None:
              with SignalRecorder(page_rendered) as recorder:
                  NextClient().get("/")
              assert len(recorder.events) == 1

Pytest plugin
-------------

``next.testing.plugin`` is a pytest plugin that carries the wiring a next.dj suite would otherwise keep in its own ``conftest.py``.
A project turns it on by naming it in ``addopts``, which loads the plugin for that suite alone.
The plugin is named explicitly rather than shipped as a ``pytest11`` entry point, so it never loads into an unrelated pytest run in the same environment and never imports the framework ahead of coverage measurement.

.. code-block:: ini
   :caption: pytest.ini

   [pytest]
   DJANGO_SETTINGS_MODULE = config.settings
   pythonpath = .
   addopts = -p next.testing.plugin
   next_pages = myapp/routes
   next_clear_cache = true

That file is the recommended starting point for a new suite, and the options it sets are described below.

Ini options
~~~~~~~~~~~

The plugin registers three ini options.
Each one is inert by default, so a suite that loads the plugin and sets none of them behaves as it would without the plugin.

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Option
     - Type
     - Effect
   * - ``next_pages``
     - paths
     - Imports every ``page.py`` beneath the listed directories once per session, before the first test runs.
   * - ``next_components``
     - bool
     - Imports every registered ``component.py`` once per session, through ``eager_load_components()``.
   * - ``next_clear_cache``
     - bool
     - Clears the default Django cache before each test.

``next_pages`` takes the ``paths`` ini type, so each entry resolves relative to the ini file and a project lists several page roots one per line.

.. code-block:: ini
   :caption: pytest.ini with two page roots

   [pytest]
   DJANGO_SETTINGS_MODULE = config.settings
   pythonpath = .
   addopts = -p next.testing.plugin
   next_pages =
       root_pages
       notes/workspaces
   next_components = true
   next_clear_cache = true

The page import happens in a session-scoped autouse fixture, which runs the ``@context`` and ``@action`` decorators before the first request reaches the router.
The loader memoises each absolute directory, so listing the same root twice imports it once.

``next_components`` matters for a suite that renders components without going through HTTP, and for a project that sets ``LAZY_COMPONENT_MODULES = True`` in ``NEXT_FRAMEWORK``.
It also registers the components folders the configured page trees carry, which a live registry otherwise holds only once a request has made the router walk that tree, so a component beside a page resolves in a test that renders it before any request.
See :ref:`ref-settings` for the description of that flag.

``next_clear_cache`` drops the default cache in an autouse fixture that runs before every test.
A suite that memoises page context or renders through a cached backend needs that isolation, and a suite that never touches the cache leaves the option off.

Fixtures
~~~~~~~~

The plugin ships one fixture a test asks for by name.

``next_client``
   A fresh ``NextClient`` for a single test.
   The name is deliberate.
   Calling it ``client`` would shadow the ``client`` fixture pytest-django provides, so a test that wants Django's plain client keeps asking for ``client`` and a test that wants the framework client asks for ``next_client``.

.. code-block:: python
   :caption: tests/test_index.py

   from next.testing import NextClient

   def test_index(next_client: NextClient) -> None:
       response = next_client.get("/")
       assert response.status_code == 200

The other two fixtures are autouse and no test names them.
``next_pages`` reads the page and component options once per session, and ``next_cache_isolation`` clears the cache before each test.
The session fixture shares its name with the ini option it reads.

Registry state between tests
----------------------------

Action and component registrations are side effects of importing ``page.py`` and ``component.py`` modules.
The canonical setup imports them once per session and leaves the registries alone between tests.
``next_pages`` in ``pytest.ini`` covers that for a pytest suite, and every project under ``examples/`` uses it.

A suite that runs outside pytest, or one that computes its page roots at runtime, calls the eager loaders from ``next.testing.loaders`` directly.

.. code-block:: python
   :caption: conftest.py, hand-rolled equivalent of ``next_pages``

   from pathlib import Path

   import pytest

   from next.testing import eager_load_pages

   PROJECT_ROOT = Path(__file__).resolve().parent

   @pytest.fixture(autouse=True, scope="session")
   def _load_pages() -> None:
       eager_load_pages(PROJECT_ROOT / "notes" / "pages")

Either route runs the ``@context`` and ``@action`` decorators before the first test dispatches a request.

.. note::

   When ``LAZY_COMPONENT_MODULES = True`` in ``NEXT_FRAMEWORK``, bulk import of ``component.py`` modules from configured component roots is skipped during ``AppConfig.ready``.
   Set ``next_components = true`` in ``pytest.ini``, or call ``eager_load_components()`` from ``next.testing.loaders`` once per session, to import every registered ``component.py`` regardless of the flag.

With the default ``LAZY_COMPONENT_MODULES = False``, the configured component roots are imported during ``AppConfig.ready``.
Components that live inside a page tree register during the URL router walk instead, which a suite rendering them before the first request never triggers.
``eager_load_components()`` registers those folders itself, so the call covers both roots and page trees and no router walk has to be staged by hand.
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

``reset_page_cache()`` resets no registry.
It calls ``Page.clear_template_caches`` to drop the composed page template.
The test environment runs with ``DEBUG`` off, and the composition cache stats its sources only under ``DEBUG``, so a template rewritten inside one process still renders from the composition built before the rewrite.

Call ``reset_page_cache()`` after any such rewrite.
Wrapping the test in ``override_settings(DEBUG=True)`` works too, because it puts the mtime check back on.
The check reaches a composition built before the override, because the mtime snapshot is recorded whether or not the process watches edits.

``reset_component_templates()`` is the component-side twin of that helper.
It calls ``ComponentsManager.clear_template_caches`` to drop the compiled template of every component.
A test that rewrites a ``.djx`` file or a ``component`` string in place needs that for the same reason.

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
   from next.testing.capture import SignalRecorder
   from next.testing.client import NextClient

   def test_emits(db) -> None:
       with SignalRecorder(action_dispatched) as recorder:
           NextClient().post_action("create_note", {"title": "hi"})
       assert len(recorder.events) == 1
       event = recorder.events[0]
       assert event.kwargs["action_name"] == "create_note"

The recorder holds a list of ``SignalEvent`` instances with ``signal``, ``sender``, and ``kwargs`` attributes.
``SignalRecorder`` accepts one or more signals and raises ``ValueError`` when constructed with none.
``events`` carries every capture in emission order, ``events_for``, ``first_for``, and ``last_for`` narrow that list to one signal, ``clear`` empties it without disconnecting, and the recorder is iterable and supports ``len()``.
See :doc:`/content/ref/testing` for the signatures.

``start()`` connects its receivers without a weak reference, and ``stop()`` is what disconnects them, so a recorder driven by hand rather than by a ``with`` statement needs its ``stop()`` call.
That holds for a plain ``SignalRecorder`` and for both wrappers below, and a recorder left running keeps appending events for the rest of the session.

Two convenience wrappers cover the common multi-signal cases.

``capture_signals(*signals)`` returns a started ``SignalRecorder`` and reads well in ``with`` statements.

.. code-block:: python
   :caption: test with capture_signals

   from next.signals import action_dispatched, page_rendered
   from next.testing.capture import capture_signals
   from next.testing.client import NextClient

   def test_dispatch_and_render(db) -> None:
       with capture_signals(action_dispatched, page_rendered) as recorder:
           NextClient().post_action("create_note", {"title": "hi"})
       assert len(recorder.events_for(action_dispatched)) == 1
       dispatch = recorder.first_for(action_dispatched)
       assert dispatch.kwargs["action_name"] == "create_note"

``capture_framework_signals()`` attaches to every name in ``next.signals.__all__``, which helps integration tests assert ordering without listing signals by hand.
It returns an already started recorder.

.. code-block:: python
   :caption: asserting emission order

   from next.signals import form_validation_failed, page_rendered
   from next.testing.capture import capture_framework_signals
   from next.testing.client import NextClient

   def test_failure_signals_before_rerender(db) -> None:
       with capture_framework_signals() as recorder:
           NextClient().post_action("create_note", {"title": ""}, origin="/")
       order = [event.signal for event in recorder]
       assert order.index(form_validation_failed) < order.index(page_rendered)

Action helpers
--------------

``next.testing.actions`` exposes ``resolve_action_url`` and ``build_form_for``.
``resolve_action_url`` turns an action name into its dispatch URL.
``build_form_for`` builds a bound form for an action so a unit test can assert validation without HTTP.
Both raise ``FormActionNotFoundError`` from ``next.forms`` for an unknown action name, with the closest registered names rendered into the message.
``build_form_for`` raises ``LookupError`` for an action registered without a form class, which covers a handler-only action and a wizard alike, and the message points at posting to ``resolve_action_url`` with the test client instead.

.. code-block:: python
   :caption: tests/test_action_helpers.py

   from next.testing.actions import build_form_for, resolve_action_url

   def test_form_validates(db) -> None:
       assert resolve_action_url("create_note").startswith("/_next/form/")
       form = build_form_for("create_note", {"title": "Direct", "body": ""})
       assert form.is_valid()

Testing a guard
---------------

``login_required`` and ``permission_required`` are enforced by the dispatcher rather than by the page that rendered the form, so the assertion belongs on a POST to the dispatch endpoint.
An untested guard is an absent guard, and the two refusals differ enough that each one earns its own test.

An anonymous caller is redirected to ``settings.LOGIN_URL`` with the validated posted origin in the ``next`` parameter.

.. code-block:: python
   :caption: tests/test_guards.py

   from django.conf import settings

   from next.testing.client import NextClient

   def test_anonymous_is_redirected_to_login(db) -> None:
       response = NextClient().post_action("delete_note", {"id": "1"}, origin="/notes/")
       assert response.status_code == 302
       assert response["Location"].startswith(settings.LOGIN_URL)
       assert "next=/notes/" in response["Location"]

An authenticated caller missing one of the declared permissions raises ``PermissionDenied``, which Django renders as a 403.

.. code-block:: python
   :caption: authenticated without the permission

   from django.contrib.auth import get_user_model

   from next.testing.client import NextClient

   def test_permission_is_enforced(db) -> None:
       user = get_user_model().objects.create_user("reader", password="secret")
       client = NextClient()
       client.force_login(user)
       response = client.post_action("delete_note", {"id": "1"}, origin="/notes/")
       assert response.status_code == 403

Granting that permission to the same user and asserting the handler runs is the third case, which is what proves the guard admits as well as refuses.
The ``check_permissions`` and ``has_object_permission`` hooks are enforced on the same endpoint and are tested the same way, except that a hook returning an ``HttpResponse`` surfaces as that response instead of a 403.
A wizard guard is enforced on every step submission, so the same two tests apply to each step URL.
See :doc:`/content/topics/forms/actions` for the declarations themselves.

HTML utilities
--------------

``next.testing.html`` provides assertions for inspecting rendered HTML fragments.
``render_component_by_name`` takes ``at``, the template path the component is referenced from, which is what drives which components are visible.
The same path is seeded as the ambient template path of the body, so a nested ``{% component %}`` resolves from it too, and a ``context`` entry naming that key wins over the seed.
Pass ``collector=`` a ``StaticCollector`` to capture the co-located assets of the component and of anything it nests.

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

``find_form`` picks a form out of a rendered page by its ``action``, by a substring the block must hold, or by one it must not hold.
``form_action`` reads the ``action`` attribute of the fragment's first tag, ``form_fields`` returns every named input as a name to value mapping, and ``hidden_fields`` narrows that to the hidden inputs the dispatcher expects echoed back on submit.

.. code-block:: python
   :caption: tests/test_form_markup.py

   from next.testing.actions import resolve_action_url
   from next.testing.client import NextClient
   from next.testing.html import find_form, form_action, form_fields, hidden_fields

   def test_create_form_carries_its_origin() -> None:
       html = NextClient().get("/").content.decode()
       form = find_form(html, action=resolve_action_url("create_note"))
       assert form_action(form) == resolve_action_url("create_note")
       assert hidden_fields(form)["_next_form_origin"] == "/"
       assert "title" in form_fields(form)

The ``_next_form_origin`` value read back here is the same field ``NextClient.post_action`` fills through its ``origin`` keyword, so a test can assert that the page renders the origin the dispatcher later re-renders.

``init_payload`` decodes the ``Next._init(...)`` bootstrap object the static manager writes into every rendered page, which is where the client runtime reads the CSRF header name and token.

.. code-block:: python
   :caption: tests/test_bootstrap.py

   from next.testing.client import NextClient
   from next.testing.html import init_payload

   def test_page_bootstraps_csrf() -> None:
       html = NextClient().get("/").content.decode()
       payload = init_payload(html)
       assert payload["$csrf"]["header"] == "X-CSRFToken"

``find_form`` and ``find_anchor`` span from the start tag to the first matching end tag, so an element written without its closing tag is never found.
Neither finder matches inside a comment or a script body.

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

A ``StaticCollectorProxy`` is yielded by ``patch_static_collector()``.
Its ``.collector`` attribute holds the collector most recently built inside the block, so a page that renders twice leaves the second one, and a test can assert on the emitted styles and scripts without parsing HTML.
Pass ``factory=`` to swap the collector implementation entirely.
The callable runs in place of the default ``create_collector`` and returns a custom ``StaticCollector`` for the duration of the block.

Use ``patch_static_collector()`` to inspect which assets a page emits.

.. code-block:: python
   :caption: tests/test_static_capture.py

   from next.testing.client import NextClient
   from next.testing.patching import patch_static_collector

   def test_collects_styles() -> None:
       with patch_static_collector() as proxy:
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
The protocol asks for ``static_can_handle`` beside ``can_handle`` and ``resolve``, and returning ``None`` from it keeps the stub a runtime candidate for every parameter.
The method is mandatory, and a stub that omits it is refused with a ``TypeError`` naming the class as ``override_provider`` hands it to the resolver.
``compile_resolve`` stays optional and lives on the separate ``CompilingParameterProvider`` protocol, so a stub that defines none still satisfies ``ParameterProvider`` and is asked for its ``resolve`` on every replay.

.. code-block:: python
   :caption: prepending a stub provider

   from next.testing.deps import resolve_call
   from next.testing.patching import override_provider

   class EveryIntIsSeven:
       def can_handle(self, param, context) -> bool:
           return param.annotation is int

       def resolve(self, param, context) -> int:
           return 7

       def static_can_handle(self, param) -> bool | None:
           return None

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
The example below points a temporary root at ``tmp_path / "_components"``, the folder that directly holds the ``info_card/`` component directory, because the scanner reads only the immediate children of a ``DIRS`` root.

.. code-block:: python
   :caption: temporary component root

   from next.testing.patching import override_component_backends
   from next.testing.rendering import render_component_by_name

   def test_component_from_temporary_root(tmp_path) -> None:
       config = {
           "BACKEND": "next.components.FileComponentsBackend",
           "DIRS": [str(tmp_path / "_components")],
       }
       with override_component_backends(config):
           html = render_component_by_name("info_card", at=tmp_path / "template.djx")
       assert "info-card" in html

Resolution context doubles
--------------------------

``next.testing.deps.make_resolution_context`` builds a ``ResolutionContext`` for unit tests on providers.
``next.testing.deps.resolve_call`` resolves a callable's dependencies and returns the kwargs mapping.
Both accept ``request``, ``form``, ``url_kwargs``, and ``context_data``, and ``make_resolution_context`` also takes ``cleaned_data`` together with a prepared ``cache`` and ``stack`` when a test wants to read either afterwards.

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
       call_command("check", fail_level="WARNING", verbosity=0)

``call_command("check")`` on its own raises only when a check reports an error, so a framework warning such as a shadowed component name passes silently.
A suite that wants the warnings gated too passes ``fail_level="WARNING"``.

Eight framework checks are registered with ``deploy=True`` and stay out of that run, three because each one imports or compiles the whole page tree, one because it describes every development checkout, and four because they are the opt-in SEO audits.
A test suite is the cheapest place to pay that cost, so a second call turns them on.

.. code-block:: python
   :caption: deployment check test

   from django.core.management import call_command

   def test_deploy_checks_pass() -> None:
       call_command("check", deploy=True, fail_level="WARNING", verbosity=0)

That run adds ``next.E017`` for a ``page.py`` that raises on import, ``next.E084`` for a ``component.py`` that does, and ``next.E072`` for a composed page template that does not compile.
All three are failures a request would otherwise surface as a 404, a stripped body, or a 500.
It also adds ``next.W083`` and the SEO audits ``next.W089`` to ``next.W096``, so a suite that gates on ``fail_level="WARNING"`` either keeps every page described and titled or silences the audits it does not want through ``SILENCED_SYSTEM_CHECKS``, see :doc:`seo/auditing`.
See :ref:`ref-system-checks` for the full catalog.

See also
--------

.. seealso::

   :doc:`/content/howto/test-a-page-with-actions` for a full end to end flow, a form validation failure, and a signal emission assertion with working code.
   :doc:`/content/ref/testing` for the public API.
   :doc:`/content/topics/signals` for the signal catalog.
