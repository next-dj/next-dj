.. _deployment:

Deployment
==========

This section covers production deployment of a next.dj project.
The pages assume an existing Django project that runs locally and walks through the checklist, static file pipeline, server choice, and recommended settings.

:doc:`checklist`
   Pre-flight checklist before shipping.

:doc:`static-files`
   How ``collectstatic`` and the static pipeline behave in production.

:doc:`wsgi-asgi`
   Choosing a server and wiring next.dj into it.

:doc:`settings`
   Production-tuned values for ``NEXT_FRAMEWORK``.

.. toctree::
   :hidden:
   :maxdepth: 1

   checklist
   static-files
   wsgi-asgi
   settings
