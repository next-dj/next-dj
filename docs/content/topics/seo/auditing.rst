.. _topics-seo-auditing:

Checking and testing SEO
========================

Three tools tell what a project hands search engines before a crawler does.
The system checks read the declarations without a request, ``manage.py showmetadata`` prints where each key of a page comes from, and ``next.testing`` asserts the head and the sitemap inside a test.
This page covers each of them.

.. contents::
   :local:
   :depth: 2

The system checks
-----------------

Every ``manage.py check`` validates the ``METADATA``, ``SITE``, and ``SEO`` scopes, the ``metadata`` of every page, and the ``sitemap.py`` and ``robots.py`` of every tree.
It reports a declaration the framework cannot serve as intended, a key of the wrong shape, a title template without a default, a route the sitemap cannot reverse, or a ``Disallow`` covering a ``noindex`` page.
The crawler checks carry the ``seo`` tag beside ``next``, so ``--tag seo`` narrows a run to them.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --deploy --tag seo

``--deploy`` adds the deployment checks of the site scope, a missing ``SITE["URL"]`` and a site closed by ``INDEXABLE`` that still publishes a sitemap or a robots source.
:doc:`/content/ref/system-checks` holds every code and its condition.

What the checks can see
~~~~~~~~~~~~~~~~~~~~~~~

The checks read the static metadata of a page, ``DEFAULTS`` and every ``metadata`` dict from the page root down.
A ``@page.metadata`` callable contributes nothing there, because running user code at check time could reach a database the deploy has yet to migrate, so its values belong to the tests.
The checks run without a request, so the indexability rule reads as it does for a static caller, see :doc:`site`.

Where a key comes from
----------------------

``manage.py showmetadata`` resolves a URL path to its page and prints which layer settles each key of its static metadata, ``DEFAULTS`` or a ``page.py``, with every ``@page.metadata`` callable of the page listed after them.

.. code-block:: bash
   :caption: shell

   uv run python manage.py showmetadata /notes/42/

Testing the head
----------------

``next.testing.assert_metadata`` parses a response and compares the head tags it names, ``None`` expecting a tag to be absent.
``og`` and ``twitter`` take property suffixes, and ``alternates`` and ``jsonld`` whole values, the members of the ``@graph`` listed as nodes.
A test is the place for everything a callable decides per row, the title of a note or the ``noindex`` of a draft.

.. code-block:: python
   :caption: notes/tests/test_seo.py

   from next.testing import NextClient, assert_metadata, parse_sitemap

   def test_note_head(note):
       response = NextClient().get(f"/notes/{note.pk}/")
       assert_metadata(
           response,
           title=f"{note.title} · Notes",
           canonical=f"https://notes.example/notes/{note.pk}/",
           robots=None,
           og={"type": "article", "title": f"{note.title} · Notes"},
       )

   def test_sitemap_lists_the_note(note):
       urls = parse_sitemap(NextClient().get("/sitemap.xml"))
       assert f"https://notes.example/notes/{note.pk}/" in {url.loc for url in urls}

``parse_sitemap`` answers the ``SitemapUrl`` values of a document, each with ``loc``, ``lastmod``, and ``alternates``.
A robots file is plain text, so a test asserts on ``response.content`` directly.
``reset_seo`` drops the discovered sources and the ``@sitemap.items`` registrations, for a test that writes a ``sitemap.py`` of its own.

See also
--------

.. seealso::

   :doc:`/content/howto/audit-seo-before-deploy` for the checks and the tests in CI.
   :doc:`/content/ref/system-checks` for every code and the ``seo`` tag.
   :doc:`/content/ref/testing` for the test helpers.
