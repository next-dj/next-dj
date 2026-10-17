.. _topics-seo-breadcrumbs:

Breadcrumbs
===========

A page tree already is a hierarchy, so the breadcrumbs of a page are its ancestor pages with a label each.
The ``breadcrumb`` metadata key names the label, ``{% breadcrumbs %}`` renders the crumbs, and the JSON-LD graph gains a ``BreadcrumbList`` without any markup of its own.

.. contents::
   :local:
   :depth: 2

Labels
------

Every ``page.py`` from the page root down to the page is one crumb candidate, root first and the page itself last.
The label of a page is its ``breadcrumb`` key, else its own title, the plain text or the ``absolute`` form with no template applied, else the page adds no crumb.
``"breadcrumb": False`` leaves a page out of the crumbs even when it carries a title.

.. code-block:: python
   :caption: blog/pages/page.py

   metadata = {"title": {"default": "Blog"}, "breadcrumb": "Home"}

.. code-block:: python
   :caption: blog/pages/posts/page.py

   metadata = {"title": "Posts"}

.. code-block:: python
   :caption: blog/pages/posts/[slug]/page.py

   from blog.models import Post

   from next import page
   from next.pages import MetadataDict

   @page.metadata
   def post_metadata(post: Post) -> MetadataDict:
       return {"title": post.title, "breadcrumb": post.short_title}

``/posts/launch-day/`` gets the crumbs ``Home``, ``Posts``, and the short title of the post.
The key is never inherited, each page is read on its own, and ``DEFAULTS`` cannot declare it.
A dynamic ancestor labels its crumb through its callable, and a callable is only run for a descendant when it is registered with ``@page.metadata(inherit=True)``, so a section whose label depends on the URL registers it that way.

URLs
----

The URL of a crumb reverses the page of its directory through the ``page_reverse`` rules, in the namespace and the URLconf the request resolved under, with the URL kwargs of the current match that the route names.
``/posts/launch-day/`` therefore links the ``Posts`` crumb to ``/posts/`` and, under :func:`~django.conf.urls.i18n.i18n_patterns`, to the prefixed path of the active language.
A crumb whose page does not reverse, or a render without a resolved request, gets no URL and renders as plain text.
The crumb whose URL is the request path is the current one.

Crumbs cross the boundary of a tree mounted under a prefix.
The deepest page of the host tree that the prefix passes through, such as the ``/dashboard/`` page for an application mounted under ``dashboard/books/``, leads the crumbs of the mounted page with its own, and that host page reaches further up the same way.
The host page is found by URL rather than by tree.
Each trail of the prefix first reverses the page of that name, and when no page carries the name, such as a trail a generic ``[str:model_name]`` page of another tree serves, the trail builds its URL from the include prefix and the matched route, each kwarg through the ``to_url`` of its converter, and :func:`~django.urls.resolve` names the page that serves it.
An application mounted under ``admin/library/book/<int:book>/`` therefore gets the crumbs of the generic changelist that serves ``/admin/library/book/``, so ``/admin/library/book/1/outline/`` reads ``Dashboard``, ``Books``, and ``Outline``.
A prefix included through :func:`~django.urls.re_path` has no route to build from, so a host page that only resolving would find gives no crumbs there.
Its crumbs fold from its metadata chain under its own URL kwargs, and its contexts never run, so a host label whose ``@page.metadata`` callable needs a context value of the host page is left out.
A host page whose access rule the request does not pass gives no crumbs at all, and a rule that names an object counts as not passed, since only the render of that page may load the object.
The host crumbs are read once something reads them, the ``{% breadcrumbs %}`` tag or the ``BreadcrumbList`` of the JSON-LD graph.
The crumbs of the mounted tree follow from its root, so ``/dashboard/books/1/history/`` reads ``Dashboard``, ``Books``, and ``History``.

The tag
-------

``{% breadcrumbs %}`` renders the crumbs as an ordered list in a labelled ``<nav>``.

.. code-block:: jinja
   :caption: blog/pages/posts/layout.djx

   <article>
     {% breadcrumbs %}
     {% template %}
   </article>

The markup is ``<nav aria-label="Breadcrumb"><ol>`` with one ``<li>`` per crumb, a link where the crumb has a URL and ``aria-current="page"`` on the current one, every label escaped.
A page without crumbs renders nothing.

``{% breadcrumbs as crumbs %}`` binds the tuple of ``Breadcrumb`` values to ``crumbs`` instead of rendering, each with ``label``, ``url``, and ``current``, for markup of the project's own.

.. code-block:: jinja
   :caption: blog/pages/posts/layout.djx

   {% breadcrumbs as crumbs %}
   <ol class="crumbs">
     {% for crumb in crumbs %}
       <li>{% if crumb.url and not crumb.current %}<a href="{{ crumb.url }}">{{ crumb.label }}</a>{% else %}{{ crumb.label }}{% endif %}</li>
     {% endfor %}
   </ol>

Both forms reuse the resolve ``{% metadata %}`` ran for the same render, so the callables run once.

The BreadcrumbList node
-----------------------

The JSON-LD graph gains a ``BreadcrumbList`` with the ``@id`` of the page URL followed by ``#breadcrumb`` when the page has at least two crumbs, every crumb above the page has a URL, and its metadata declares no ``BreadcrumbList`` of its own.
Each crumb becomes a ``ListItem`` with its position, its label, and its absolute URL.
A site that describes its crumbs elsewhere declares a ``BreadcrumbList`` of its own, which takes the place of the derived one.

See also
--------

.. seealso::

   :doc:`structured-data` for the graph the node joins.
   :doc:`/content/topics/url-reversing` for the reverse rules the crumb URLs follow.
   :doc:`/content/ref/template-tags` for the tag forms.
