Testing
=======

.. _testing:

next.dj is designed to be tested with the native Django and pytest stack. The
project ships ``next.testing`` — a thin, framework-agnostic layer that covers
the handful of things the stdlib tools do not: signal capture, action-name
dispatch, filesystem page loading, in-isolation page/component rendering, DI
overrides, and merge-friendly ``NEXT_FRAMEWORK`` settings patches. Nothing in
the package imports pytest, so the helpers work from ``pytest``,
``django.test.TestCase``, and stdlib ``unittest`` alike.

Django / pytest-django first
----------------------------

Most HTTP-level tests are plain Django. ``NextClient`` is a
``django.test.Client`` subclass, so everything Django and pytest-django give
you works out of the box:

* HTTP assertions: ``response.status_code``, ``assertRedirects``,
  ``assertContains(response, html, html=True)``, ``assertInHTML``,
  ``assertTemplateUsed``.
* Response introspection: ``response.context[...]``, ``response.resolver_match``.
* Requests without a view: ``django.test.RequestFactory`` or the
  pytest-django ``rf`` fixture.
* Settings: pytest-django ``settings`` fixture or ``django.test.override_settings``.
* Database isolation: pytest-django ``db`` / ``transactional_db`` / ``django_db_setup``.
* Email: pytest-django ``mailoutbox``.

Reach for ``next.testing`` when those native tools do not cover the case.

When to use ``next.testing``
----------------------------

================================================   =============================================
Need                                               Helper
================================================   =============================================
Dispatch a form action by its registration name    :class:`NextClient <next.testing.NextClient>`
                                                   (``post_action``, ``get_action_url``)
Observe framework signals                          :class:`SignalRecorder <next.testing.SignalRecorder>`,
                                                   :func:`capture_signals <next.testing.capture_signals>`,
                                                   :func:`capture_framework_signals <next.testing.capture_framework_signals>`
Pick the right ``<a>`` out of a rendered page and
check a class token                                :func:`find_anchor <next.testing.find_anchor>`,
                                                   :func:`assert_has_class <next.testing.assert_has_class>`,
                                                   :func:`assert_missing_class <next.testing.assert_missing_class>`
Render a page or component in isolation            :func:`render_page <next.testing.render_page>`,
                                                   :func:`render_component_by_name <next.testing.render_component_by_name>`
Unit-test a provider or a DI-aware callable        :func:`resolve_call <next.testing.resolve_call>`,
                                                   :func:`make_resolution_context <next.testing.make_resolution_context>`
Unit-test a form without HTTP                      :func:`build_form_for <next.testing.build_form_for>`
Override a DI dependency for a block               :func:`override_dependency <next.testing.override_dependency>`,
                                                   :func:`override_provider <next.testing.override_provider>`
Merge keys into ``NEXT_FRAMEWORK`` without
replacing the full dict                            :func:`override_next_settings <next.testing.override_next_settings>`,
                                                   :func:`override_component_backends <next.testing.override_component_backends>`
Stub a form action for a block                     :func:`override_form_action <next.testing.override_form_action>`
Inspect collected static assets                    :func:`patch_static_collector <next.testing.patch_static_collector>`
Import every ``page.py`` before URL dispatch       :func:`eager_load_pages <next.testing.eager_load_pages>`
Reset registries or the page template cache
after swapping settings                            :func:`reset_registries <next.testing.reset_registries>`,
                                                   :func:`reset_page_cache <next.testing.reset_page_cache>`
================================================   =============================================

Recipes
-------

HTTP action dispatch + signal assertion
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from next.forms.signals import action_dispatched
   from next.testing import NextClient, capture_signals

   def test_create_link(client):
       with capture_signals(action_dispatched) as rec:
           response = client.post_action("create_link", {"url": "https://next.dj"})
       assert response.status_code in (200, 302)
       assert rec.first_for(action_dispatched).kwargs["action_name"] == "create_link"

HTML assertions — prefer native Django
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from django.test import SimpleTestCase
   from django.test.utils import assertInHTML, assertContains

   def test_home_lists_cta(client):
       response = client.get("/")
       assertContains(response, '<h1>Welcome</h1>', count=1, html=True)
       assertInHTML('<a href="/signup/">Join</a>', response.content.decode(), count=1)

Edge-case anchor lookup + class-token checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Django's HTML-aware matchers require an exact fragment. When the template
emits a class list generated from settings or state and you want to assert
that *one specific* token is present or absent, reach for the anchor helpers:

.. code-block:: python

   from next.testing import assert_has_class, assert_missing_class, find_anchor

   def test_active_nav(client):
       html = client.get("/admin/").content.decode()
       assert_has_class(find_anchor(html, href="/admin/", text="Links"), "font-semibold")
       assert_missing_class(find_anchor(html, href="/admin/stats/", text="Stats"), "font-semibold")

In-isolation rendering
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from next.testing import render_component_by_name, render_page

   def test_post_page_renders_body(tmp_path):
       html = render_page(tmp_path / "blog" / "[slug]" / "page.py", slug="hello")
       assert "Hello, world" in html

   def test_link_card_component(tmp_path):
       html = render_component_by_name(
           "link_card",
           at=tmp_path / "shortener" / "routes" / "page.py",
           context={"link": some_link},
       )
       assert "link-card" in html

Unit-testing a provider / DI callable
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from next.testing import resolve_call

   def test_flag_provider_raises_without_flag_name():
       from myapp.flags.providers import FlagProvider  # noqa: F401

       with pytest.raises(LookupError):
           resolve_call(component_that_needs_a_flag)

Overrides
~~~~~~~~~

.. code-block:: python

   from next.testing import override_dependency, override_next_settings

   def test_flag_override(client):
       with override_dependency("current_user", admin_user):
           response = client.get("/admin/")
       assert response.status_code == 200

   def test_with_lazy_components():
       with override_next_settings(LAZY_COMPONENT_MODULES=True):
           assert_current_settings_flag_is(True)

``override_next_settings`` merges the supplied keys into the existing
``NEXT_FRAMEWORK`` dict (unlike Django's ``@override_settings``, which would
replace the full value), and fires ``settings_reloaded`` so framework
managers pick up the change.

conftest.py skeleton
--------------------

Apps can copy this skeleton. ``next.testing`` does not ship a pytest plugin,
so the scaffolding stays in the user's ``conftest.py``.

.. code-block:: python

   import os
   import sys
   from pathlib import Path

   import django
   import pytest
   from django.conf import settings
   from django.core.cache import cache

   from next.testing import NextClient, eager_load_pages

   ROOT = Path(__file__).resolve().parent
   for p in (ROOT, ROOT.parent):
       if str(p) not in sys.path:
           sys.path.insert(0, str(p))
   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
   if not settings.configured:
       django.setup()

   PAGES_DIR = ROOT / "<app>" / "<routes_dir>"


   @pytest.fixture(autouse=True, scope="session")
   def _load_pages() -> None:
       eager_load_pages(PAGES_DIR)


   @pytest.fixture(autouse=True)
   def _isolate(db) -> None:
       cache.clear()


   @pytest.fixture
   def client() -> NextClient:
       return NextClient()

Troubleshooting
---------------

**Signals arrive empty.**
   Ensure the receiver is connected during the scope you are measuring.
   ``SignalRecorder`` / ``capture_signals`` connect on ``__enter__`` and
   disconnect on ``__exit__``. Emissions outside the ``with`` block are not
   recorded.

**``resolve_action_url`` raises ``KeyError`` for a real action.**
   The page module that registered the action was never imported. Call
   ``eager_load_pages`` inside a session-scoped fixture.

**Two tests in the same session raise ``ImproperlyConfigured: UID collision``.**
   Two different action names hash to the same UID. Rename one of them, or
   call :func:`reset_form_actions <next.testing.reset_form_actions>` /
   :func:`reset_registries <next.testing.reset_registries>` for a clean slate.

**Tests that swap ``NEXT_FRAMEWORK`` settings ignore the new backend.**
   The component manager caches backends. Call
   :func:`reset_components <next.testing.reset_components>` (or
   :func:`reset_registries <next.testing.reset_registries>`) after changing
   settings, or use :func:`override_component_backends
   <next.testing.override_component_backends>` which invalidates
   automatically via ``settings_reloaded``.

**``render_page`` returns stale HTML after rewriting a ``page.py`` on disk.**
   ``page.render`` memoises composed template strings per file path. Call
   :func:`reset_page_cache <next.testing.reset_page_cache>` between
   iterations that mutate the same file.
