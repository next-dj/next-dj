.. _howto-publish-a-sitemap:

Publish a sitemap
=================

Problem
-------

A crawler should find every public page of the site with the date each one last changed, and the private pages should be neither listed nor blocked by mistake.

Solution
--------

Install ``django.contrib.sitemaps``, set the public origin in ``base``, put a ``sitemap.py`` at the top of the page root, list the database-backed routes with ``@sitemap.items``, add a ``robots.py`` beside it, and mount ``next.seo.urls`` at the host root when the router sits under a prefix.

Walkthrough
-----------

Install the sitemaps application
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The XML comes from the templates of :doc:`django:ref/contrib/sitemaps`, so the application joins ``INSTALLED_APPS`` and the Django template backend keeps ``APP_DIRS``.
``base`` gives every listed URL its scheme and host.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       "django.contrib.sitemaps",
       "next",
       "notes",
   ]

   NEXT_FRAMEWORK = {
       "METADATA": {
           "DEFAULTS": {"base": "https://notes.example"},
       },
   }

Switch the sitemap on
~~~~~~~~~~~~~~~~~~~~~

A ``sitemap.py`` at the top of the page root lists every static route on its own.
The file below sets a default change frequency and keeps a scratch section out.

.. code-block:: python
   :caption: notes/pages/sitemap.py

   changefreq = "weekly"
   exclude = ["scratch/**"]

``/``, ``/about/``, and every other route without a bracket segment is now in the document.
A page whose metadata declares ``"robots": {"index": False}`` stays out without an ``exclude`` entry.

List the database routes
~~~~~~~~~~~~~~~~~~~~~~~~

A route with a parameter has no URLs until the file names them.
``@sitemap.items`` binds a callable to the trail, and each ``Entry`` carries the kwargs that reverse it and the date the row last changed.

.. code-block:: python
   :caption: notes/pages/sitemap.py

   from collections.abc import Iterator

   from notes.models import Note

   from next.seo import Entry, sitemap

   changefreq = "weekly"
   exclude = ["scratch/**"]

   @sitemap.items("notes/[int:note_id]")
   def notes() -> Iterator[Entry]:
       rows = Note.objects.filter(published=True).only("pk", "updated_at")
       for note in rows:
           yield Entry(kwargs={"note_id": note.pk}, lastmod=note.updated_at)

The trail is spelled the way the directories spell it.
``only()`` keeps the query to the two columns the entries need, because the callable runs on every uncached request.
A catalog of many rows adds ``cache = 300`` to the file, which serves the document from the default cache for five minutes.

Add the robots file
~~~~~~~~~~~~~~~~~~~

An empty ``robots.py`` renders allow-all plus the ``Sitemap:`` line.
The file below also keeps crawlers off the search results, whose every query is a URL of its own.

.. code-block:: python
   :caption: notes/pages/robots.py

   from next.seo import Rule

   rules = [Rule(user_agent="*", disallow=["/search/"])]

The edit pages keep their ``noindex`` metadata and stay crawlable.
A ``Disallow`` over them would hide the tag from the crawler, and ``next.W101`` reports one.

Mount the routes at the host root
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Skip this step when ``include("next.urls")`` sits at the root of the URLconf, because the routes are already there.
A router under a prefix or inside :func:`~django.conf.urls.i18n.i18n_patterns` takes the routes with it, so the same three routes are mounted at the root through ``next.seo.urls``.

.. code-block:: python
   :caption: config/urls.py

   from django.conf.urls.i18n import i18n_patterns
   from django.urls import include, path

   urlpatterns = [
       path("", include("next.seo.urls")),
       *i18n_patterns(path("", include("next.urls"))),
   ]

Verification
------------

Run the checks first.
A trail the tree does not route, a dynamic route the file forgot, and a robots ``Disallow`` covering a listed URL are all reported here rather than by the crawler.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

Start the server and read both documents.

.. code-block:: bash
   :caption: shell

   uv run python manage.py runserver
   curl -s http://127.0.0.1:8000/sitemap.xml
   curl -s http://127.0.0.1:8000/robots.txt

The sitemap lists every static route and one ``<url>`` per published note, each ``<loc>`` starting with ``https://notes.example`` and each note carrying a ``<lastmod>``.
The robots text ends with ``Sitemap: https://notes.example/sitemap.xml``.
Publish a note, request the sitemap again, and the new URL is in the document, after the cache window when the file declares one.

See also
--------

.. seealso::

   :doc:`/content/topics/seo/sitemaps` for the module attributes, the index, and the origin rules.
   :doc:`/content/topics/seo/robots` for the static ``robots.txt`` form and the one-source rule.
   :doc:`/content/howto/internationalize-routes` for the ``i18n`` attribute that lists every language.
   :doc:`/content/deployment/checklist` for the deploy-time steps.
