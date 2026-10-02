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
``reset_scripts`` drops every discovered ``scripts.py`` and ``reset_seo`` the discovered SEO sources and the ``@sitemap.items`` registrations, for a test that rewrites a source, since outside ``DEBUG`` each one is read once.

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

Metadata and SEO
~~~~~~~~~~~~~~~~

``assert_metadata(response, **expected)`` parses the head of a response or an HTML string and compares the tags it names, ``title``, ``description``, ``keywords``, ``viewport``, ``robots``, ``googlebot``, ``canonical``, ``alternates``, ``jsonld``, ``og``, and ``twitter``.
``None`` expects a tag to be absent, ``og`` and ``twitter`` take a dict of property suffixes, and ``jsonld`` lists every node, the members of the ``@graph`` flattened in.
``og=None`` and ``twitter=None`` expect no tag of that block at all.
An unknown key raises ``TypeError``, and every mismatch lands in one ``AssertionError``.
The head is read by ``head_tags(html)`` of ``next.testing.metadata`` into a ``HeadTags`` value, and parsing stops at ``</head>``, so a tag in the body never counts.
The read is strict, so a single-valued tag rendered twice, a second ``<title>`` or canonical link among them, and a JSON-LD script that holds no JSON raise ``HeadParseError``, a ``ValueError``, rather than reading the first value.
A response is decoded with its own charset and a streaming one is read to its end, the same way ``parse_sitemap`` reads one.
``parse_sitemap`` answers the ``SitemapUrl`` values of a sitemap or an index response, each with ``loc``, ``lastmod``, and ``alternates``, both exported from ``next.testing``.
A body that is no well-formed XML raises ``xml.etree.ElementTree.ParseError``, so a malformed sitemap fails the test rather than reading as empty.
A robots file is plain text, so a test reads ``response.content`` directly.
``reset_seo`` in the isolation helpers drops the discovered SEO sources and the ``@sitemap.items`` registrations.

.. automodule:: next.testing.metadata
   :members:

.. automodule:: next.testing.seo
   :members:
   :exclude-members: SitemapUrl

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
