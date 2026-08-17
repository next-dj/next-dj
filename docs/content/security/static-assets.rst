.. _security-static-assets:

Static asset security
=====================

This page covers the security properties of the static pipeline including origin control, content hashing, integrity attributes, and CSP nonces.

.. contents::
   :local:
   :depth: 2

Origin control
--------------

The static pipeline only serves files that the discovery scanner registered.
Random URLs that match the ``STATIC_URL`` prefix but do not match a registered asset return ``404``.

A custom backend can refuse to serve files that originate outside trusted directories.
Add the directory whitelist to the backend and reject every path that falls outside.

An empty ``STATIC_BACKENDS`` falls back to the bundled ``StaticFilesBackend``, and ``manage.py check`` reports ``next.W030`` so the missing chain stays visible.

Content hash
------------

The default ``StaticFilesBackend`` resolves asset URLs through Django staticfiles.
Pair it with ``ManifestStaticFilesStorage`` so each URL carries a content hash slice.
Two consequences flow from the hash.

Cache busting.
   A change in the file content changes the URL, which forces every browser cache miss.

Tamper detection.
   A mismatch between the stored hash and the served bytes indicates either a deployment bug or an active attack.

Run ``uv run python manage.py collectstatic`` during deployment.
Combined with ``ManifestStaticFilesStorage`` the framework cooperates with the standard Django manifest pipeline.

Subresource Integrity
---------------------

Subresource Integrity (SRI) attributes let the browser verify the file contents before executing scripts or applying stylesheets.
A custom backend overrides the renderer methods to add the ``integrity`` and ``crossorigin`` attributes.

.. code-block:: python
   :caption: notes/backends.py

   import base64
   import hashlib
   from django.contrib.staticfiles.storage import staticfiles_storage
   from next.static import StaticFilesBackend

   class SriBackend(StaticFilesBackend):
       def render_link_tag(self, url, *, request=None) -> str:
           integrity = self._integrity_for(url)
           return f'<link rel="stylesheet" href="{url}" integrity="{integrity}" crossorigin>'

       def _integrity_for(self, url: str) -> str:
           relative_path = url.removeprefix(staticfiles_storage.base_url)
           with staticfiles_storage.open(relative_path) as asset:
               digest = hashlib.sha384(asset.read()).digest()
           return "sha384-" + base64.b64encode(digest).decode("ascii")

Apply the same pattern to ``render_script_tag`` and ``render_module_tag``.

The integrity attribute belongs to the server-rendered tag, so it covers a full page render.
An asset a patch envelope brings for the first time is built by the client runtime from a fixed attribute set that carries no ``integrity`` and no ``crossorigin``, see :doc:`/content/topics/partial-rendering/limitations`.
A deployment that must verify every byte through SRI serves the assets of a partial-rendered page on the full render, for example by declaring them on the layout rather than co-locating them with a zone.

Content Security Policy
-----------------------

A strict Content Security Policy denies inline ``<script>`` and ``<style>`` unless they carry a nonce.

Use a context processor to publish the nonce.

.. code-block:: python
   :caption: notes/context_processors.py

   import secrets

   def csp_nonce(request) -> dict:
       nonce = secrets.token_urlsafe(16)
       request._csp_nonce = nonce
       return {"csp_nonce": nonce}

Bake the nonce into the backend tag templates so every collected ``<script>`` and ``<link>`` carries it.
A request aware backend reads the nonce from the request and writes it into each tag.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend

   class NonceBackend(StaticFilesBackend):
       def render_script_tag(self, url, *, request=None) -> str:
           nonce = getattr(request, "_csp_nonce", "")
           return f'<script src="{url}" nonce="{nonce}"></script>'

Register the backend by its dotted path.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "STATIC_BACKENDS": [
           {
               "BACKEND": "notes.backends.NonceBackend",
               "OPTIONS": {},
           }
       ]
   }

Send the matching ``Content-Security-Policy`` header from middleware.

The runtime script and the nonce
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The backend covers the collected assets only.
The ``next.min.js`` tag and the inline ``Next._init`` script come from the runtime script builder, which formats its templates once per process and cannot carry a per-request nonce.
Under a strict CSP switch ``NEXT_JS_OPTIONS`` to the ``MANUAL`` policy and emit the three fragments from a template tag that reads the nonce off the request.

Co-located JS
-------------

Component JS files run in the global browser context.
Avoid using component scripts for sensitive operations such as form submission with secret tokens.
Keep auth state in cookies flagged ``HttpOnly`` so component scripts cannot read it.

Inline init payload
-------------------

The runtime injects serialised JS context through an inline ``<script>`` that calls ``Next._init``.
The framework escapes ``<``, ``>``, ``&``, U+2028, and U+2029 in that payload, so a serialised value that contains ``</script>`` cannot break out of the tag.
The remaining risk is visibility, since serialised values appear in the page source, not tag breakout.
Never mark a secret ``serialize=True``.

Cross origin resource sharing
-----------------------------

CORS is a Django middleware concern.
The framework does not add CORS headers on its own.
Configure ``django-cors-headers`` or an equivalent middleware when the static origin serves cross site requests.

Common pitfalls
---------------

Inline script without nonce under strict CSP.
   The browser drops the script.
   Add the nonce attribute or move the script into a co-located file.

Mixed content on a CDN host.
   A ``https://`` page that loads a ``http://`` CDN asset triggers mixed content blocking.
   Serve the CDN assets over HTTPS in production.

Long lived service worker.
   A service worker that caches by path keeps stale assets when the hash changes.
   Key the cache on the full URL, which already carries the content hash in the filename.

See also
--------

.. seealso::

   :doc:`/content/topics/static-assets/backends` for backend customisation.
   :doc:`/content/deployment/static-files` for the production pipeline.
   :doc:`/content/topics/static-assets/js-context` for ``NEXT_JS_OPTIONS``, ``ScriptInjectionPolicy``, and CSP-related templates.
   :ref:`Runtime script options <topics-static-js-runtime-script-options>` for the ``MANUAL`` policy and the script builder.
