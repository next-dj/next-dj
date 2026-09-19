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
A name is resolved through the active static backend, which hands it to Django staticfiles.
Every other reference reaches the document byte for byte as it was written.

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

Where the rule applies
----------------------

Every surface that takes an asset reference resolves it.

- ``{% use_style %}``, ``{% use_script %}``, and ``{% use_module %}`` in a template, see :doc:`template-tags`.
- A module-level ``styles`` or ``scripts`` list in ``page.py`` or ``component.py``, see :ref:`topics-static-module-lists`.
- ``{% asset %}``, which returns the URL for a raw ``href`` or ``src`` instead of registering anything.

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

Resolution and caching
----------------------

A name is resolved when the asset is registered, not when the tag is injected, so the collector, the dedup strategy, and a partial patch envelope all see the public URL.
The default backend memoises each resolved reference for the life of the process, alongside the memo it keeps for co-located files.
A ``STATIC_ROOT``, ``STATIC_URL``, or ``STORAGES`` change drops both through ``forget_urls``, so a test that swaps storage through ``override_settings`` sees fresh URLs on the next render.

Asset discovery caches the plan it built for a page or a component, and that plan holds the authored references rather than the resolved URLs.
A settings change therefore moves the URLs a cached plan produces without rebuilding the plan.

In a module list the kind is inferred from the authored reference, so ``scripts = ["site/app.mjs"]`` is a ``module`` asset whatever the manifest does to the filename.

.. _topics-static-name-resolution-backend:

Resolving a name
----------------

``StaticBackend.resolve_url`` is the single seam that turns a reference into a URL.
It is concrete on the base class and returns the reference unchanged, so a backend subclassing ``StaticBackend`` directly resolves nothing until it overrides the method.
``StaticFilesBackend`` overrides it with the rule above.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend, is_static_name

   class BuildManifestBackend(StaticFilesBackend):
       def resolve_url(self, reference: str) -> str:
           if not is_static_name(reference):
               return reference
           built = self._manifest.get(reference)
           return built if built is not None else super().resolve_url(reference)

``next.static.is_static_name`` is the predicate the default backend applies, exported so a custom backend answers the same shapes core does.
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
