.. _security:

Security
========

next.dj inherits the Django security model and adds a few subsystem specific surfaces.

:doc:`overview`
   The threat model inherited from Django and the additions specific to next.dj.

:doc:`csrf-and-forms`
   CSRF protection through the ``{% form %}`` tag and the re-render pipeline.

:doc:`sessions-and-auth`
   What the framework does with the session and the authenticated user, and what it leaves to Django.

:doc:`static-assets`
   Origin, hash, and integrity for shipped CSS and JS.

:doc:`csp-and-nonce`
   Serving the client runtime under a Content Security Policy.

:doc:`di-and-untrusted-input`
   Treating URL, query, and form values as untrusted.

:doc:`file-uploads`
   Limits, validation, and serving rules for user-supplied files.

:doc:`reporting`
   How to disclose a vulnerability privately.

.. toctree::
   :hidden:
   :maxdepth: 1

   overview
   csrf-and-forms
   sessions-and-auth
   static-assets
   csp-and-nonce
   di-and-untrusted-input
   file-uploads
   reporting
