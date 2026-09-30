.. _topics-scripts:

Third-party scripts and consent
===============================

A marketing site runs third-party scripts and asks the visitor for consent before the ones that track.
The core ships the mechanism, a ``scripts.py`` that declares every script of a page tree, a consent gate that keeps a script inert until its category is granted, a CSP nonce on every tag the framework writes, and one navigation event per page view.
It ships no vendor integration, and a vendor adapter is a ``Script`` plus a few lines of JavaScript, see :doc:`/content/howto/write-a-vendor-adapter`.

The framework offers the mechanism and not compliance, and every category but ``necessary`` stays denied until the visitor chooses.

:doc:`declaring`
   ``scripts.py``, the ``Script`` value, the loading strategies, the head slot, and the CSP nonce.

:doc:`consent`
   The switch that turns consent on, the categories, the consent backend, server and client rendering, ``{% #consented %}``, and ``Next.consent``.

:doc:`page-views`
   ``next:navigated``, the one event a partial navigation announces, and how a vendor counts page views from it.

.. toctree::
   :hidden:
   :maxdepth: 1

   declaring
   consent
   page-views

.. seealso::

   :doc:`/content/howto/write-a-vendor-adapter` for Plausible and GA4 under consent.
   :doc:`/content/ref/scripts` for the ``next.scripts`` and ``next.consent`` API.
   :doc:`/content/ref/client-extras` for ``Next.ready("scripts")``, ``Next.consent``, ``Next.scripts``, and ``Next.navigation``.
   :doc:`/content/security/csp-and-nonce` for the nonce under a Content Security Policy.
