.. _howto-intercepting-modal:

Open a page as a modal over another
===================================

Problem
-------

A photo feed links to a page for each photo.
Clicking a photo should open it in a modal over the feed, while a reload of the photo URL or a shared link should render the full photo page.
The modal needs its own template, its own access rule, and its own title, and the photo page must stay unchanged.

Solution
--------

Add an intercepting directory beside the feed.
A directory named ``(..)photo`` targets the ``photo`` page one level above the feed, and the page below it serves a zone of that target.
A link with ``data-next-layer`` names the zone, and the framework answers the zone request with the intercept page instead of the target.

Walkthrough
-----------

Lay out the tree
~~~~~~~~~~~~~~~~

The scope of an intercept is the URL trail of the directory that holds it, here ``feed``.
``(..)`` drops one segment of that trail, so ``(..)photo`` targets ``photo`` and the directories below it extend the target to ``photo/[int:photo]``.
The brackets must spell the target exactly, so ``[int:photo]`` on both sides.

.. code-block:: text
   :caption: gallery/pages/

   feed/
     page.py
     template.djx
     (..)photo/
       [int:photo]/
         page.py
         template.djx
   photo/
     [int:photo]/
       page.py
       template.djx

``(.)`` targets a page below the directory that holds the intercept, and ``(...)`` targets a trail from the root of the page tree.
An intercept is never routed, so it adds no URL, no URL name, and no sitemap entry.

Write the intercept page
~~~~~~~~~~~~~~~~~~~~~~~~

The template wraps its content in a zone and carries no layout.
A zone response is the zone alone, so a ``layout.djx`` in the intercept directory never renders.

.. code-block:: jinja
   :caption: gallery/pages/feed/(..)photo/[int:photo]/template.djx

   {% zone "photo-modal" %}
   <article class="modal">
     <h1>{{ photo.title }}</h1>
     <img src="{{ photo.image.url }}" alt="{{ photo.title }}">
   </article>
   {% endzone %}

The page module reads the row the way the target does.
The URL kwargs of a zone request are the ones of the target URL, so ``photo`` is the captured segment.

.. code-block:: python
   :caption: gallery/pages/feed/(..)photo/[int:photo]/page.py

   from gallery.models import Photo

   from next import context, page
   from next.deps import DObject
   from next.pages import MetadataDict

   page.login_required()
   page.permission_required("gallery.view_photo", obj=DObject[Photo])

   metadata: MetadataDict = {"title": "Photo"}

   @context("photo")
   def photo(photo: DObject[Photo]) -> Photo:
       return photo

The intercept answers to its own access chain and to the chain of the target.
The stricter wins, and a refusal of the intercept is the response, with no fallback to the target.
``metadata`` titles the modal, and the client restores the title of the feed when the modal closes.

Link to the photo
~~~~~~~~~~~~~~~~~

The link keeps the honest URL of the photo and names the zone.

.. code-block:: jinja
   :caption: gallery/pages/feed/template.djx

   {% for photo in photos %}
     <a href="/photo/{{ photo.pk }}/" data-next-layer="photo-modal">
       <img src="{{ photo.thumb.url }}" alt="{{ photo.title }}">
     </a>
   {% endfor %}

The runtime sends a zone request for ``/photo/7/`` with the name ``photo-modal``.
The photo page declares no such zone, so the intercept that declares it serves the request.
A link without ``data-next-layer``, a reload, and a shared link all render the photo page.

Verification
------------

Click a photo in the feed.
The modal shows the intercept template, the address bar reads ``/photo/7/``, and Back closes the modal.
Reload the address and the full photo page renders with its own layout and head.

List the routes.

.. code-block:: bash
   :caption: shell

   uv run python manage.py routes

The row of the intercept follows the row of its target, with the same URL, the ``page.py`` of the intercept, and the zone in the ``Intercept`` column.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

The checks name a mistake that would leave the intercept silent.
``next.E170`` reports a target no page of the tree routes, and ``next.E173`` a zone the target or a sibling intercept shares.
``next.E174`` reports an intercept without a standalone zone, and ``next.E176`` a kwarg or an object permission the target URL cannot supply.
``next.W138`` reports a breadcrumb, ``headers``, or ``cache`` that a zone response never carries, and ``next.W139`` a ``layout.djx`` inside the intercept directory.
``next.W140`` reports an intercept that falls under the default login while its target is public.

See also
--------

.. seealso::

   :doc:`/content/topics/access` for the access chains of an intercept.
   :doc:`/content/topics/partial-rendering/layers` for the layer runtime and its titles.
   :doc:`/content/topics/partial-rendering/zones` for the ``{% zone %}`` tag.
   :doc:`/content/security/sessions-and-auth` for the security notes.
   :doc:`/content/ref/system-checks` for the intercept checks.
