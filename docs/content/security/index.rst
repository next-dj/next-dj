.. _security:

Security
========

next.dj inherits the Django security model and adds a few subsystem specific surfaces.
This section covers how each surface protects against common attacks and how to harden a deployment.

:doc:`overview`
   The threat model inherited from Django and the additions specific to next.dj.

:doc:`csrf-and-forms`
   CSRF protection through the ``{% form %}`` tag and the re-render pipeline.

:doc:`static-assets`
   Origin, hash, and integrity for shipped CSS and JS.

:doc:`csp-and-nonce`
   Serving the partial runtime under a Content Security Policy.

:doc:`di-and-untrusted-input`
   Treating URL, query, and form values as untrusted.

:doc:`reporting`
   How to disclose a vulnerability privately.

.. toctree::
   :hidden:
   :maxdepth: 1

   overview
   csrf-and-forms
   static-assets
   csp-and-nonce
   di-and-untrusted-input
   reporting
