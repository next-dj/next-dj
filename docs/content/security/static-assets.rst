.. _security-static-assets:

Static Asset Security
=====================

This page covers the security properties of the static pipeline including origin control, content hashing, integrity attributes, and CSP nonces.

.. contents::
   :local:
   :depth: 2

Origin Control
--------------

The static pipeline only serves files that the discovery scanner registered.
Random URLs that match the ``STATIC_URL`` prefix but do not match a registered asset return ``404``.

A custom backend can refuse to serve files that originate outside trusted directories.
Add the directory whitelist to the backend and reject every path that falls outside.

Content Hash
------------

The default ``StaticBackend`` appends a SHA-256 hash slice to every asset URL.
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
A custom backend adds the ``integrity`` and ``crossorigin`` attributes.

.. code-block:: python
   :caption: notes/backends.py

   import base64
   import hashlib

   from next.static.backends import StaticBackend
   from next.static.assets import Asset


   class SriBackend(StaticBackend):
       def integrity(self, asset: Asset) -> str:
           digest = hashlib.sha384(asset.content).digest()
           return "sha384-" + base64.b64encode(digest).decode()

       def render_link_tag(self, asset: Asset, **attrs: str) -> str:
           url = self.url(asset)
           integrity = self.integrity(asset)
           return f'<link rel="stylesheet" href="{url}" integrity="{integrity}" crossorigin>'

Apply the same pattern to ``render_script_tag`` and ``render_module_tag``.

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

Pass the nonce on every inline tag and on every ``{% collect_styles %}`` and ``{% collect_scripts %}`` call.

.. code-block:: jinja
   :caption: layout

   {% collect_styles nonce=csp_nonce %}
   {% collect_scripts nonce=csp_nonce %}

   {% #inline_script nonce=csp_nonce %}
     console.log("hello");
   {% /inline_script %}

Send the matching ``Content-Security-Policy`` header from middleware.

Co-located JS
-------------

Component JS files run in the global browser context.
Avoid using component scripts for sensitive operations such as form submission with secret tokens.
Keep auth state in cookies and HTTP only attributes.

Cross Origin Resource Sharing
-----------------------------

CORS is a Django middleware concern.
The framework does not add CORS headers on its own.
Configure ``django-cors-headers`` or an equivalent middleware when the static origin serves cross site requests.

Common Pitfalls
---------------

Inline script without nonce under strict CSP.
   The browser drops the script.
   Add the nonce attribute or move the script into a co-located file.

Mixed content on hash query string.
   A ``http://`` page that loads a ``https://`` CDN asset triggers mixed content warnings.
   Serve the HTML over HTTPS in production.

Long lived service worker.
   A service worker that caches by path keeps stale assets when the hash changes.
   Key the cache on the full URL including the hash query string.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/backends` for backend customisation.
   :doc:`/content/deployment/static-files` for the production pipeline.
