.. _ref-testing:

Testing Reference
=================

Module Summary
--------------

``next.testing`` exposes a test client, signal recorder, registry isolation, action helpers, HTML utilities, rendering helpers, loaders, patching helpers, and dependency context builders.

Public API
----------

Client
~~~~~~

``NextClient`` extends Django's test client with form-action shortcuts for end to end HTTP tests.

.. automodule:: next.testing.client
   :members:

Signals
~~~~~~~

``SignalRecorder`` and the ``capture_signals`` wrappers capture framework signal payloads inside a context manager.

.. automodule:: next.testing.signals
   :members:

Isolation
~~~~~~~~~

``reset_registries`` and its narrower variants clear the framework registries between tests.
``reset_form_registration_state`` additionally clears the registration diagnostics and the cached wizard backend.

.. automodule:: next.testing.isolation
   :members:

Actions
~~~~~~~

``resolve_action_url`` and ``build_form_for`` exercise a registered action without crafting POST bodies.

.. automodule:: next.testing.actions
   :members:

Rendering
~~~~~~~~~

``render_page`` and ``render_component_by_name`` render a single page or component without an HTTP round trip.

.. automodule:: next.testing.rendering
   :members:

Loaders
~~~~~~~

``eager_load_components``, ``eager_load_pages``, and ``clear_loaded_dirs`` force-import or reset the per-directory memoisation of ``page.py`` and ``component.py`` modules in tests.

.. automodule:: next.testing.loaders
   :members:

HTML Utilities
~~~~~~~~~~~~~~

``find_anchor``, ``assert_has_class``, and ``assert_missing_class`` inspect rendered HTML fragments.

.. automodule:: next.testing.html
   :members:

Patching
~~~~~~~~

The ``override_*`` context managers and ``patch_static_collector`` swap framework wiring for the duration of a block.

.. automodule:: next.testing.patching
   :members:

Dependencies
~~~~~~~~~~~~

``make_resolution_context`` and ``resolve_call`` build dependency-injection test doubles for provider unit tests.

.. automodule:: next.testing.deps
   :members:

See Also
--------

.. seealso::

   :doc:`/content/topics/testing` for the topic guide.
   :doc:`/content/howto/test-a-page-with-actions` for a recipe.
