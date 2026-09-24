.. _topics-static-name-resolution:

Name resolution
===============

Every asset reference the pipeline accepts is read as a Django staticfiles name when it looks like a name, and as a finished URL otherwise.
A named asset then behaves exactly like a co-located one, because both travel the same staticfiles storage, the same backend, and the same memo.

.. contents::
   :local:
   :depth: 2

The rule
--------

A reference is a name when it carries no scheme, no host, no query, and no fragment, and its path is non-empty and does not start with a slash.
A name is normalised, checked against the staticfiles root it is relative to, and resolved through the active static backend, which hands it to Django staticfiles.
Every other reference reaches the document as it was written.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   {% use_style "site/tokens.css" %}
   {% use_style "https://cdn.example.com/reset.css" %}

The first reference is a name, so it resolves to ``/static/site/tokens.css`` under a plain ``STATIC_URL``, to ``/static/site/tokens.4f2ac1d8.css`` under a hashed manifest, and to the CDN host when ``STATIC_URL`` names one.
The second reference carries a scheme, so it renders unchanged.

References that pass through
----------------------------

The shapes below are never treated as names.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Reference
     - Why it passes through
   * - ``/static/site/app.css``
     - The path starts with a slash, so it is already root relative.
   * - ``//cdn.example.com/app.css``
     - The reference carries a host, so it is protocol relative.
   * - ``https://cdn.example.com/app.css``
     - The reference carries a scheme.
   * - ``data:text/css,body{}``
     - The reference carries a scheme, and a data URL has no host at all.
   * - ``site/app.css?v=2``
     - The reference carries a query.
   * - ``?v=1`` and ``#anchor``
     - The path is empty.
   * - ``C:\app.css``
     - The URL parser reads the drive letter as a scheme, so a Windows path is left alone.
   * - The empty string
     - The path is empty, and the registration tags drop an empty value before resolution.

A reference that must stay literal is therefore spelled as one of these shapes.
Writing ``/static/site/app.css`` by hand stays valid, at the price of ignoring ``STATIC_URL`` and the manifest.
A project that sets ``NEXT_FRAMEWORK["STATIC_VERSION"]`` still stamps its ``v`` pair on the rendered URL, which replaces a ``v`` the reference already carries and leaves a ``data:`` or ``blob:`` reference alone, see :doc:`/content/ref/settings`.

Where the rule applies
----------------------

Every surface that takes an asset reference resolves it.

- ``{% use_style %}``, ``{% use_script %}``, and ``{% use_module %}`` in a template, see :doc:`template-tags`.
- A module-level ``styles`` or ``scripts`` list in ``page.py`` or ``component.py``, see :ref:`topics-static-module-lists`.
- ``{% asset %}``, which returns the URL for a raw ``href`` or ``src`` instead of registering anything.
- ``Patches.add_asset`` in a partial patch builder, so an asset a patch introduces carries the URL a full render would have written, see :ref:`ref-partial`.

The inline block forms ``{% #use_style %}`` and ``{% #use_script %}`` take no reference, because their body is the asset.
Raw markup the project writes by hand is not scanned, so a ``<link href="...">`` written outside a tag reaches the browser as typed.

What a name buys
----------------

A name is resolved by the same code path a co-located ``component.css`` goes through, so four things arrive with it.

``STATIC_URL`` applies.
   Moving the static prefix, or pointing it at a CDN host, moves every named asset with it and needs no template edit.

The manifest applies.
   Under :doc:`ManifestStaticFilesStorage <django:ref/contrib/staticfiles>` a named asset carries the content hash that a hardcoded public path silently skips.

Deduplication collapses the spellings.
   ``UrlDedup`` keys on the resolved URL, so a name and a literal URL that resolve to the same string emit one tag, see :doc:`deduplication`.

A custom backend sees the reference.
   A project resolving assets from a build manifest answers for names too, see `Resolving a name`_ below.

When a name does not resolve
----------------------------

A name the staticfiles manifest does not hold raises ``StaticAssetNotFoundError``, a ``RuntimeError`` exported from ``next.static``.
The error leaves the render rather than dropping the tag, and a co-located file missing from the same manifest fails on exactly the same terms.
The message names the unresolved path and points at ``collectstatic``.

The plain ``StaticFilesStorage`` resolves any name without checking that a file exists, so a typo renders a URL that answers 404.
Under a manifest the same typo raises.
Run ``manage.py collectstatic`` and ``manage.py findstatic <name>`` before trusting a name that only ever ran under the development server.

.. warning::

   A name is relative to the staticfiles root, not to ``STATIC_URL``.
   With ``STATIC_URL = "static/"`` the reference ``static/app.css`` resolves to ``/static/static/app.css``, because the leading segment is part of the name.
   Drop the prefix from the reference and let the setting supply it.

Names use forward slashes.
A backslash is not normalised, and it resolves under a plain storage while raising under a manifest, so the two environments would disagree.

A name that leaves the root
---------------------------

The root is enforced rather than assumed.
A ``..`` segment that stays inside the root is normalised away, so ``site/css/../app.css`` is looked up as ``site/app.css``.
A reference that climbs above the root raises ``StaticAssetTraversalError``, so ``{% asset "a/../../media/x.css" %}`` aborts the render rather than printing a URL outside the static tree.

The error subclasses Django's ``SuspiciousFileOperation``, itself a :exc:`~django.core.exceptions.SuspiciousOperation`, which Django answers with HTTP 400 and logs under ``django.security``.
It is exported as ``next.static.StaticAssetTraversalError``, and it carries the offending reference as written on its ``reference`` attribute.
A reference built from stored or user-supplied data is therefore refused at the door rather than rendered, see :doc:`/content/security/static-assets`.

The name a co-located file gets
-------------------------------

A co-located file arrives from the other direction, with the framework choosing its public name instead of the project writing one.
Discovery and the staticfiles finder share one ``PathResolver``, so both sides settle on the same name, and every name lands under the ``next/`` staticfiles namespace.

``template`` role.
   The directory of the template relative to its page root, and ``index`` for the page root itself.
   A ``template.css`` in ``notes/pages/archive/`` is published as ``next/archive.css``, and one beside the root ``template.djx`` as ``next/index.css``.

``layout`` role.
   The directory of the layout relative to its page root with ``/layout`` appended, and ``layout`` alone for the page root itself.
   A ``layout.css`` in ``notes/pages/archive/`` is published as ``next/archive/layout.css``.

``component`` role.
   ``components/<name>``, where the name is the component directory name.
   A ``component.css`` in ``_components/note_card/`` is published as ``next/components/note_card.css``.

The stem is no part of the name, so two stems of one role in one directory compete for a single path, see :doc:`/content/howto/add-a-custom-stem`.

.. warning::

   A component name is its directory name and nothing else, so the page tree above it does not scope it.
   Two ``_components/card/`` folders under different page trees therefore both resolve to ``next/components/card.css``, the finder keeps whichever it walked first, and the other file never reaches ``STATIC_ROOT`` or the browser.
   Give component directories names that are unique across the project.

Resolution and caching
----------------------

A name is resolved when the asset is registered, not when the tag is injected, so the collector, the dedup strategy, and a partial patch envelope all see the public URL.
The default backend memoises each resolved reference in the bounded cache it also keeps for co-located files, which evicts its stalest entry once full rather than holding every answer for the life of the process.
A ``STATIC_ROOT``, ``STATIC_URL``, or ``STORAGES`` change drops both through ``forget_urls``, so a test that swaps storage through ``override_settings`` sees fresh URLs on the next render.

Asset discovery caches the plan it built for a page or a component, and the assets a module list contributes are resolved once with that plan rather than once per render.
A ``STATIC_ROOT``, ``STATIC_URL``, or ``STORAGES`` change rebuilds those plans along with the memos, so a cached plan hands out no URL read through the previous storage.

In a module list the kind is inferred from the authored reference, so ``scripts = ["site/app.mjs"]`` is a ``module`` asset whatever the manifest does to the filename.

.. _topics-static-name-resolution-backend:

Resolving a name
----------------

``StaticBackend.resolve_url`` is the single seam that turns a reference into a URL.
It is concrete on the base class and returns the reference unchanged, so a backend subclassing ``StaticBackend`` directly resolves nothing until it overrides the method.
``StaticFilesBackend`` overrides it with the rule above.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend, static_name

   class BuildManifestBackend(StaticFilesBackend):
       def resolve_url(self, reference: str) -> str:
           name = static_name(reference)
           if name is None:
               return reference
           built = self._manifest.get(name)
           return built if built is not None else super().resolve_url(reference)

``next.static.static_name`` is what the default backend resolves through, and a custom lookup calls it and handles the ``None`` it answers for a reference that is already a URL, see :ref:`ref-static` for the full return contract.
``resolve_url`` and ``asset_url`` carry different jobs.
``resolve_url`` turns an authored reference into a public URL once per reference, while ``asset_url`` rewrites an already resolved URL on every render, which is where a per-request scheme belongs.
See :doc:`backends` for the full contract.

See also
--------

.. seealso::

   :doc:`template-tags` for the tags that accept a reference.
   :doc:`co-located-files` for module-level ``styles`` and ``scripts`` lists.
   :doc:`/content/howto/ship-a-site-wide-stylesheet` for a stylesheet every page carries.
   :doc:`/content/deployment/static-files` for ``collectstatic``, the manifest, and the CDN host.
