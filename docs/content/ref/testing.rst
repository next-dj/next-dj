.. _ref-testing:

Testing reference
=================

Module summary
--------------

``next.testing`` exposes a test client, partial envelope decoding, signal recorder, registry isolation, action helpers, HTML utilities, rendering helpers, loaders, patching helpers, and dependency context builders.
``next.testing.plugin`` sits apart from that surface as an opt-in pytest plugin and is the only module in the package that imports pytest.

Public API
----------

.. automodule:: next.testing
   :members:
   :no-index:

Pytest plugin
~~~~~~~~~~~~~

``next.testing.plugin`` registers the ``next_pages``, ``next_components``, and ``next_clear_cache`` ini options together with the ``next_client`` fixture, once a project adds ``-p next.testing.plugin`` to its pytest ``addopts``.

.. automodule:: next.testing.plugin
   :members:

Client
~~~~~~

``NextClient`` extends Django's test client with form-action shortcuts and ``get_zones`` for end to end HTTP tests.
The module also ships ``envelope_of`` and ``PartialEnvelope`` for decoding patch responses.

.. automodule:: next.testing.client
   :members:

Signal capture
~~~~~~~~~~~~~~

``SignalRecorder`` and the ``capture_signals`` wrappers capture framework signal payloads inside a context manager.
They live in ``next.testing.capture``, which leaves the name ``signals`` to the framework signal modules the recorder connects to.

.. automodule:: next.testing.capture
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
``at`` drives visibility and seeds the ambient path the body composes from, so a nested ``{% component %}`` resolves from it as well, the way a field component does under :ref:`topics-forms-field-components-composition`.
The ``collector`` keyword takes a ``StaticCollector`` that catches the co-located assets of the component itself and of anything it nests, which is the one ambient value ``context`` cannot state without naming a private key.
The ``page_module_path`` keyword names the ``page.py`` a page-scoped ``{% form %}`` or ``{% action_url %}`` in the body resolves against.
A ``context`` entry naming a seeded key wins over the seed, so a test that wants a different ambient value states it there.

.. automodule:: next.testing.rendering
   :members:

Loaders
~~~~~~~

``eager_load_components``, ``eager_load_pages``, and ``clear_loaded_dirs`` force-import or reset the per-directory memoisation of ``page.py`` and ``component.py`` modules in tests.

.. automodule:: next.testing.loaders
   :members:

HTML utilities
~~~~~~~~~~~~~~

``find_anchor``, ``find_form``, ``assert_has_class``, and ``assert_missing_class`` inspect rendered HTML fragments, while ``form_action``, ``form_fields``, ``hidden_fields``, and ``init_payload`` pull submit targets, input values, and the ``Next._init`` bootstrap payload out of a rendered page.
``find_anchor`` and ``find_form`` locate the element with the standard library HTML parser and return its verbatim source span, so an element needs its end tag to be found and markup inside a comment or a script body is never matched.

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

See also
--------

.. seealso::

   :doc:`/content/topics/testing` for the topic guide.
   :doc:`/content/howto/test-a-page-with-actions` for a recipe.
   :doc:`/content/howto/test-a-component-in-isolation` for rendering one component without HTTP.
