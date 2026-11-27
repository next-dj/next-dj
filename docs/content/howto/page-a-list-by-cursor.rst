.. _howto-page-a-list-by-cursor:

Page a list by cursor
=====================

Problem
-------

A feed lists posts newest first and loads more as the visitor scrolls.
New posts arrive while the visitor reads, so every page number shifts by the rows inserted above it.
The next page then repeats rows the visitor already saw, a deleted post makes it skip one, and a deep page makes the database walk every row before it.

Solution
--------

Page the feed with a ``KeysetPager``.
Each link then carries an opaque ``?cursor=<token>`` that names the position of a row under the ordering, and the next batch is the rows past that position, whatever was inserted or deleted elsewhere in the list.
The pager is a stateless module-level object, a context callable takes it through ``Annotated[Paging, <pager>]``, and the template hands the ``Window`` it returns to ``{% more %}``.

Walkthrough
-----------

Choose between a cursor and a page number
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Need
     - ``NumberPager``
     - ``KeysetPager``
   * - Rows inserted or deleted while a visitor scrolls
     - Rows repeat or go missing across pages.
     - Every row is shown once.
   * - A deep position in a long list
     - The database reads and discards every row before the page.
     - The database seeks to the position through an index.
   * - A link to page 7, or numbered page links
     - ``?page=7`` is addressable.
     - A position is opaque, and only the next and the previous batch are linked.
   * - The ordering
     - Any ordering the queryset or the sequence already has.
     - Local non-null columns of the model, the last one unique.

A list that is read by scrolling and changes while it is read pages by cursor.
A list with numbered page links, or one ordered by an annotation or a nullable column, pages by number.

Page the rows
~~~~~~~~~~~~~

Declare the pager once at module level, name it in the annotation of the context callable, and return the window.

.. code-block:: python
   :caption: feed/pages/feed/page.py

   from typing import Annotated

   from feed.models import Post

   from next import context
   from next.partial import KeysetPager, Paging, Window

   latest = KeysetPager(("-created", "-pk"), per_page=20)

   @context("rows")
   def rows(page: Annotated[Paging, latest]) -> Window[Post]:
       return page.paginate(Post.objects.filter(published=True))

The queryset carries the filters and the pager applies the ordering itself, so an ``order_by()`` on the queryset is not read.
The window iterates over its rows, so the template loops over it and passes the same value to the tag.

.. code-block:: jinja
   :caption: feed/pages/feed/template.djx

   {% zone "feed" tag="ul" %}
     {% for post in rows %}
       <li data-next-key="{{ post.pk }}">{{ post.title }}</li>
     {% endfor %}
   {% endzone %}

   {% more zone="feed" next=rows %}Show more{% empty %}End of the feed.{% endmore %}

The tag below the list links ``?cursor=<token>`` for the batch after the rows and fetches it when the link scrolls into view.
A header that shows the total builds the pager with ``count=True`` and reads ``rows.count``, at the price of one counting query a render.

Keep the address and the memory in step
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Two options of the tag make a long list behave after many batches.

.. code-block:: jinja
   :caption: feed/pages/feed/template.djx

   {% more zone="feed" previous=rows %}Show earlier{% endmore %}

   {% zone "feed" tag="ul" %}
     {% for post in rows %}
       <li data-next-key="{{ post.pk }}">{{ post.title }}</li>
     {% endfor %}
   {% endzone %}

   {% more zone="feed" next=rows prune=40 url="replace" %}Show more{% empty %}End of the feed.{% endmore %}

``url="replace"`` writes ``?cursor=<token>&_restore=cursor`` into the address bar after each batch, and a reload of that address renders the batch the token names with the whole batches before it, ``max_restore`` batches of the pager at most, ten by default.
``prune=40`` drops whole batches from the top once the list holds more than 40 rows, and the tag above the list loads them back.

Follow the ordering rule
~~~~~~~~~~~~~~~~~~~~~~~~

The ordering is a total one, so no two rows share a position.

- Every name is a concrete column of the model itself, with a leading ``-`` for descending.
  A foreign key is named by its attname, as in ``author_id``.
  A lookup across a relation and an annotation are not columns of the model.
- No column is nullable, since a ``NULL`` never compares and its rows would never page.
- The last column is unique, usually ``pk``, so rows tied on every other column still have an order.

A broken ordering raises at build time or on the first window read, with the column and the rule in the message, see :ref:`topics-partial-rendering-reference-more`.
``("-created",)`` alone is refused, because two posts created in the same microsecond would tie, and ``("-created", "-pk")`` is the usual shape.

Index the ordering
~~~~~~~~~~~~~~~~~~

The first batch is an ``ORDER BY`` with a ``LIMIT``.
Every later batch adds a filter that seeks past the position of the token.

.. code-block:: sql
   :caption: the shape of the query for a batch after a position

   SELECT ... FROM feed_post
   WHERE published
     AND (created < %s OR (created = %s AND id < %s))
     AND created <= %s
   ORDER BY created DESC, id DESC
   LIMIT 21

The last bound repeats the leading column on purpose, so a database that cannot plan the ``OR`` as a range still starts its index scan at the position.
The limit is one row past the batch, which tells the pager whether another batch follows without a count.

That query needs a composite index over the ordering columns in the ordering's order, after any column the queryset filters by equality.

.. code-block:: python
   :caption: feed/models.py

   from django.db import models
   from django.utils import timezone

   class Post(models.Model):
       title = models.CharField(max_length=200)
       published = models.BooleanField(default=False)
       created = models.DateTimeField(default=timezone.now)

       class Meta:
           indexes = [
               models.Index(fields=["published", "-created", "-id"], name="post_feed"),
           ]

The link above the list reads the same index in the opposite direction.
An ordering that mixes directions, as in ``("-created", "name", "pk")``, needs an index whose columns carry the same mix, which :class:`~django.db.models.Index` spells with the same ``-`` prefix.
Without the index every batch sorts the filtered table, and the cursor keeps its stability but loses its cost advantage over a page number.

Page from an async callable
~~~~~~~~~~~~~~~~~~~~~~~~~~~

An ``async def`` context callable awaits ``apaginate()``, which reads the queryset through its async API and returns the same window.

.. code-block:: python
   :caption: feed/pages/feed/page.py, the async form

   @context("rows")
   async def rows(page: Annotated[Paging, latest]) -> Window[Post]:
       return await page.apaginate(Post.objects.filter(published=True))

The pager, the annotation, and the template stay as they are.

Verification
------------

Run the system checks.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

``next.E235`` reports a ``{% more %}`` with ``prune=`` or ``url=`` on a page whose context callables take no pager, and ``next.E236`` reports two pagers of one page that read the same query parameter, which a ``param=`` of its own on each pager clears.

Open the feed and read the link below the list, whose ``href`` is ``?cursor=`` followed by a token.
Scroll until two batches have loaded, and the address bar shows ``?cursor=<token>&_restore=cursor``.
Publish a post in another tab, scroll on, and the next batch repeats no row.
Reload the page, and the list renders the batches up to the one the address names.
Open ``?cursor=broken``, and the feed renders its first batch.

See also
--------

.. seealso::

   :ref:`topics-partial-rendering-reference-more` for the options of ``{% more %}``, the fields of a ``Window``, and the windows each query reads.
   :ref:`topics-partial-rendering-reference-tokens` for a signed or shortened token and a pager of another scheme.
   :ref:`ref-partial-infinite-lists` for the API of ``Pager``, ``NumberPager``, ``KeysetPager``, ``Paging``, and ``Window``.
   :doc:`/content/topics/partial-rendering/scenarios` for the infinite scroll of a catalogue paged by number.
   :doc:`read-query-parameters` for the rest of the query string.
   :repo:`search-catalog <tree/main/examples/search-catalog>` for a paged catalogue with ``NumberPager`` and :repo:`audit-forms <tree/main/examples/audit-forms>` for an audit log paged by ``KeysetPager``.
