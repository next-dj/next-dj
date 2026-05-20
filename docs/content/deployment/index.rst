.. _deployment:

Deployment
==========

This section covers production deployment of a next.dj project.
The pages assume an existing Django project that runs locally.
They walk through the checklist, the static file pipeline, the server choice, and the recommended settings.

.. rubric:: Preparing a Release

:doc:`checklist`
   Pre-flight checklist before shipping.

:doc:`settings`
   Production-tuned values for ``NEXT_FRAMEWORK``.

.. rubric:: Pipeline and Server

:doc:`static-files`
   How ``collectstatic`` and the static pipeline behave in production.

:doc:`wsgi-asgi`
   Choosing a server and wiring next.dj into it.

.. toctree::
   :hidden:
   :maxdepth: 1

   checklist
   settings
   static-files
   wsgi-asgi
