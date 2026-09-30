.. _topics-scripts-page-views:

Page views
==========

An analytics vendor counts a page view each time the visitor lands somewhere new.
Partial rendering changes the address without a page load, a filter replaces it and a layer pushes one, so a vendor tag that counts only page loads misses those views.
The runtime announces every such change as one ``next:navigated`` event, and a vendor adapter turns it into a page view.

.. contents::
   :local:
   :depth: 1

The event
---------

``next:navigated`` fires on the document and on the ``Next.on`` bus once per change of address or title, after the new content is in the document.

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Field
     - Meaning
   * - ``url``, ``path``, ``title``
     - Where the page stands now, the same values ``Next.navigation.current()`` answers.
   * - ``action``
     - ``push`` or ``replace`` for a history write, ``pop`` for a Back or Forward gesture, and ``none`` for a change of the title alone.

The event does not fire for the page load itself.
A vendor adapter counts the page it loads on from ``Next.navigation.current()`` and one more view for every ``next:navigated`` whose ``action`` is not ``none``.

.. code-block:: javascript
   :caption: shop/static/shop/pageviews.js

   const Next = window.Next;

   window.plausible("pageview", { u: Next.navigation.current().url });
   Next.on("next:navigated", ({ url, action }) => {
     if (action !== "none") window.plausible("pageview", { u: url });
   });

A layer that opens under its own address fires with ``push``, and closing it fires again for the host address, with ``replace`` or with ``pop`` when Back closed it.
An adapter that should not count the return to the host compares ``path`` with the path it reported before the layer opened.

Commits
-------

The history write of a patch envelope waits for the envelope to commit, so a layer whose body never arrives, or one the visitor closes first, leaves no history entry and fires no event.
All the writes of one envelope fold into one event, in whatever order the envelope lists its ``url`` and ``meta`` operations, and the title in the event is the one the page shows.

Vendor tags that watch history
------------------------------

Several vendor tags count a page view on every history change by themselves, GA4 under Enhanced Measurement and the default Plausible script among them.
Beside an adapter on ``next:navigated`` each navigation then counts twice.
Load the vendor's manual variant, or turn its history tracking off, and let the adapter be the one source of page views.

See also
--------

.. seealso::

   :doc:`/content/howto/write-a-vendor-adapter` for complete Plausible and GA4 adapters.
   :doc:`/content/ref/client-extras` for ``next:navigated`` and ``Next.navigation``.
   :doc:`/content/topics/partial-rendering/reference` for the ``url`` and ``meta`` verbs.
