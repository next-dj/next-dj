Testing
=======

.. _testing:

next.dj ships a framework-agnostic testing toolkit in ``next.testing``. The same
API works from ``pytest``, ``django.test.TestCase``, and stdlib
``unittest.TestCase``. Nothing in the package imports pytest, so users are free
to wire the helpers into whichever test runner their project uses.

Overview
--------

The package exposes five building blocks.

``SignalRecorder``
   Context manager that captures every emission of one or more Django signals.
   Use it to assert that routes, forms, or renders reach the expected points
   of the pipeline without installing receivers by hand.

``eager_load_pages``
   Imports every ``page.py`` under a directory so decorators such as
   ``@context`` and ``@forms.action`` register before URL dispatch runs.
   Idempotent per absolute path.

``reset_registries``
   Opt-in helper for the small number of tests that swap backends mid-session.
   Most tests do not need to call it.

``resolve_action_url`` / ``build_form_for``
   Public shortcuts around ``form_action_manager``. Resolve a form action by
   name without importing the singleton yourself.

``NextClient``
   Django test client with ``post_action`` and ``get_action_url`` methods for
   targeting form actions by registration name.

Integration scenarios
---------------------

The package does not depend on pytest at import time. Each scenario below
exercises the same API through a different harness.

.. tab-set::

   .. tab-item:: pytest

      .. code-block:: python

         from next.signals import action_dispatched
         from next.testing import NextClient, SignalRecorder

         def test_create_link():
             client = NextClient(enforce_csrf_checks=False)
             with SignalRecorder(action_dispatched) as rec:
                 client.post_action("create_link", {"url": "https://next.dj"})
             assert len(rec) == 1

   .. tab-item:: django.test

      .. code-block:: python

         from django.test import TestCase

         from next.signals import action_dispatched
         from next.testing import NextClient, SignalRecorder

         class CreateLinkTests(TestCase):
             def test_create_link(self):
                 client = NextClient(enforce_csrf_checks=False)
                 with SignalRecorder(action_dispatched) as rec:
                     client.post_action("create_link", {"url": "https://next.dj"})
                 self.assertEqual(len(rec), 1)

   .. tab-item:: unittest

      .. code-block:: python

         import unittest

         from django.dispatch import Signal

         from next.testing import SignalRecorder

         class SignalTests(unittest.TestCase):
             def test_captures(self):
                 sig = Signal()
                 with SignalRecorder(sig) as rec:
                     sig.send(sender="s", payload=42)
                 assert len(rec) == 1

conftest.py pattern
-------------------

Every next.dj app can follow the same skeleton. Replace the domain-specific
parts between angle brackets.

.. code-block:: python

   import pytest
   from pathlib import Path
   from django.core.cache import cache
   from next.testing import eager_load_pages, NextClient

   APP_DIR = Path(__file__).parent / "<domain_app>"

   @pytest.fixture(autouse=True, scope="session")
   def _load_pages():
       eager_load_pages(APP_DIR / "<pages_dir>")

   @pytest.fixture(autouse=True)
   def _isolate():
       cache.clear()
       yield

   @pytest.fixture
   def client():
       return NextClient()

API reference
-------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Name
     - Summary
   * - ``next.testing.SignalRecorder``
     - Record every emission of one or more Django signals within a scope.
   * - ``next.testing.SignalEvent``
     - Frozen dataclass describing one captured emission.
   * - ``next.testing.eager_load_pages``
     - Import every ``page.py`` under a directory. Idempotent per path.
   * - ``next.testing.clear_loaded_dirs``
     - Drop the memoisation cache used by ``eager_load_pages``.
   * - ``next.testing.reset_form_actions``
     - Clear every form-action registry reachable from ``form_action_manager``.
   * - ``next.testing.reset_components``
     - Drop cached component backends so the next render reloads them.
   * - ``next.testing.reset_registries``
     - Run the two helpers above in one call.
   * - ``next.testing.resolve_action_url``
     - Return the reverse URL for a registered form action.
   * - ``next.testing.build_form_for``
     - Instantiate the form class registered for an action name.
   * - ``next.testing.NextClient``
     - ``django.test.Client`` with ``post_action`` and ``get_action_url``.

Troubleshooting
---------------

**Signals arrive empty.**
   Ensure the receiver is connected during the scope you are measuring.
   ``SignalRecorder`` connects on ``__enter__`` and disconnects on ``__exit__``.
   Emissions outside the ``with`` block are not recorded.

**``resolve_action_url`` raises ``KeyError`` for a real action.**
   The page module that registered the action was never imported. Call
   ``eager_load_pages`` inside a session-scoped fixture.

**A second test in the same session raises ``ImproperlyConfigured: UID collision``.**
   Two different action names hash to the same UID. Rename one of them. Call
   ``reset_form_actions()`` from :mod:`next.testing` if you need a clean slate
   (or ``reset_registries()`` to drop every cached registry at once).

**Tests that swap ``NEXT_FRAMEWORK`` settings ignore the new backend.**
   The component manager caches backends. Call ``reset_components`` (or
   ``reset_registries``) after changing settings.
