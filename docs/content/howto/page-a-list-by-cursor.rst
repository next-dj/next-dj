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

``url="replace"`` writes ``?cursor=<token>&restore=1`` into the address bar after each batch, and a reload of that address renders the batch the token names with the whole batches before it, ``DEFER["MORE"]["MAX_RESTORE"]`` batches at most.
``prune=40`` drops whole batches from the top once the list holds more than 40 rows, and the tag above the list loads them back.

Follow the ordering rule
~~~~~~~~~~~~~~~~~~~~~~~~

The ordering is a total one, so no two rows share a position.

- Every name is a concrete column of the model itself, with a leading ``-`` for descending.
  A foreign key is named by its attname, as in ``author_id``.
  A lookup across a relation and an annotation are not columns of the model.
- No column is nullable, since a ``NULL`` never compares and its rows would never page.
- The last column is unique, usually ``pk``, so rows tied on every other column still have an order.

An ordering that is a bare string, empty, or names a column twice raises ``ValueError`` when the pager is built.
An ordering the model breaks raises :exc:`~django.core.exceptions.ImproperlyConfigured` on the first window read from that model, with the column and the rule in the message.
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

Sign or shorten the tokens
~~~~~~~~~~~~~~~~~~~~~~~~~~

A token is unpadded base64url of compact JSON that holds a direction and the values of the ordering columns of one row.
It is neither signed nor encrypted.
A visitor can therefore read those values and write a position of their own, which moves the window within the rows the queryset already serves and reaches no other row.
Each value is converted and validated by its model field before it reaches the query, and a token the pager cannot read falls back to the first batch instead of raising.

``encode()`` and ``decode()`` are the two methods to override when that default does not fit.
``encode(values, *, reverse)`` receives the column values of a row and the direction, and returns the token.
``decode(token)`` returns the direction and the raw values, or ``None`` for a token it refuses.
A token contains no whitespace, since the tokens of a restored window travel space-separated in one attribute, and it stays within ``max_token`` characters, 512 unless the pager is built with another cap.

Signing stops a visitor from writing positions, which matters when the ordering column is a value the page does not otherwise show.

.. code-block:: python
   :caption: feed/paging.py

   from typing import override

   from django.core import signing

   from next.partial import KeysetPager

   class SignedPager(KeysetPager):
       signer = signing.Signer(salt="feed.cursor")

       @override
       def encode(self, values: tuple[object, ...], *, reverse: bool) -> str:
           return self.signer.sign(super().encode(values, reverse=reverse))

       @override
       def decode(self, token: str) -> tuple[bool, tuple[object, ...]] | None:
           try:
               raw = self.signer.unsign(token)
           except signing.BadSignature:
               return None
           return super().decode(raw)

A signature hides nothing, so a column that must stay secret calls for encryption in the same two methods.
A token signed under a key that has since left ``SECRET_KEY`` and ``SECRET_KEY_FALLBACKS`` reads the first batch, like any other token the pager refuses.

A list ordered by one integer column fits a far shorter token.

.. code-block:: python
   :caption: feed/paging.py

   class IdPager(KeysetPager):
       def __init__(self, *, per_page: int = 20) -> None:
           super().__init__(("pk",), per_page=per_page)

       @override
       def encode(self, values: tuple[object, ...], *, reverse: bool) -> str:
           return f"{'b' if reverse else 'a'}{values[0]}"

       @override
       def decode(self, token: str) -> tuple[bool, tuple[object, ...]] | None:
           head, digits = token[:1], token[1:]
           if head not in {"a", "b"} or not (digits.isascii() and digits.isdecimal()):
               return None
           return head == "b", (int(digits),)

The pager checks what ``decode()`` returns the same way it checks the default, one value per ordering column, each converted by its field, so a codec only has to parse.

Write a pager of another scheme
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A list that pages by neither a number nor a position subclasses ``Pager``.
The subclass implements ``plan(rows, request)``, which returns a ``Plan``, and keeps no request state, since one instance serves every render.
A plan holds the slices the window reads and a ``finish`` step that builds the ``Window`` from what they fetched, and it reads nothing itself.
``window`` and ``awindow`` are the base class's own, one running the plan on the thread and the other awaiting it, so a pager written this way serves a sync and an async callable with the decision written once.
A directory that loads one initial letter at a time is such a scheme.

.. code-block:: python
   :caption: people/paging.py

   import string
   from functools import partial
   from typing import override

   from django.db.models import QuerySet
   from django.http import HttpRequest

   from next.partial import PagedRows, Pager, Plan, Window

   LETTERS = string.ascii_lowercase

   class LetterPager(Pager):
       def __init__(self, field: str, *, param: str = "letter") -> None:
           super().__init__(param=param, per_page=1, count=False)
           self.field = field

       def _letter(self, request: HttpRequest | None) -> str:
           raw = "" if request is None else request.GET.get(self.param, "")
           return raw if len(raw) == 1 and raw in LETTERS else LETTERS[0]

       @override
       def plan[T](self, rows: PagedRows[T], request: HttpRequest | None) -> Plan[T]:
           if not isinstance(rows, QuerySet):
               msg = "LetterPager pages a queryset."
               raise TypeError(msg)
           letter = self._letter(request)
           lookup = {f"{self.field}__istartswith": letter}
           section = rows.filter(**lookup).order_by(self.field, "pk")
           return Plan((section,), None, partial(self._finish, letter))

       def _finish[T](
           self, letter: str, fetched: list[list[T]], count: int | None
       ) -> Window[T]:
           [found] = fetched
           at = LETTERS.index(letter)
           following = LETTERS[at + 1] if at + 1 < len(LETTERS) else None
           return Window(
               rows=found,
               param=self.param,
               next=following,
               previous=LETTERS[at - 1] if at else None,
               back=letter if following else None,
           )

The base constructor checks ``param`` and ``per_page``, and a window of this pager is one section, so ``per_page`` is ``1``.
The first field of the plan, ``slices``, holds one queryset here, the section of the letter, and the second, ``total``, holds the source to count or ``None`` for no count, which the pager here never asks.
``finish`` receives the rows of each slice in order and the count, and returns the window.
A query value the pager does not recognise reads the first window, as the shipped pagers do, and ``request`` is ``None`` for a render without a request.

The fields of the ``Window`` are the contract with ``{% more %}``.
``next`` and ``previous`` are the tokens of the batches around the rows, ``None`` where the list ends.
``back`` is the token of the batch right before the one ``next`` fetches, here the letter of the window itself, and ``prune=`` reads it to point the link above the list at the right batch once rows are dropped.
A pager that also serves ``url=`` reads ``restore=1`` from the query, renders the batches before the token up to ``DEFER["MORE"]["MAX_RESTORE"]``, and fills ``backs`` with the same token for every batch after the first.
``LetterPager`` leaves that out, so a reload of a restored address renders the one section its letter names and the link above it loads the earlier ones.

The callable and the template use it like a shipped pager.

.. code-block:: python
   :caption: people/pages/people/page.py

   from typing import Annotated

   from people.models import Person
   from people.paging import LetterPager

   from next import context
   from next.partial import Paging, Window

   directory = LetterPager("name")

   @context("people")
   def people(page: Annotated[Paging, directory]) -> Window[Person]:
       return page.paginate(Person.objects.all())

Verification
------------

Run the system checks.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

``next.E235`` reports a ``{% more %}`` with ``prune=`` or ``url=`` on a page whose context callables take no pager, and ``next.E236`` reports two pagers of one page that read the same query parameter, which a ``param=`` of its own on each pager clears.

Open the feed and read the link below the list, whose ``href`` is ``?cursor=`` followed by a token.
Scroll until two batches have loaded, and the address bar shows ``?cursor=<token>&restore=1``.
Publish a post in another tab, scroll on, and the next batch repeats no row.
Reload the page, and the list renders the batches up to the one the address names.
Open ``?cursor=broken``, and the feed renders its first batch.

See also
--------

.. seealso::

   :ref:`topics-partial-rendering-reference-more` for the options of ``{% more %}``, the fields of a ``Window``, and the windows each query reads.
   :ref:`ref-partial-infinite-lists` for the API of ``Pager``, ``NumberPager``, ``KeysetPager``, ``Paging``, and ``Window``.
   :doc:`/content/topics/partial-rendering/scenarios` for the infinite scroll of a catalogue paged by number.
   :doc:`/content/ref/settings` for ``DEFER["MORE"]["MAX_RESTORE"]``.
   :doc:`read-query-parameters` for the rest of the query string.
   :repo:`search-catalog <tree/main/examples/search-catalog>` for a paged catalogue with ``NumberPager`` and :repo:`audit-forms <tree/main/examples/audit-forms>` for an audit log paged by ``KeysetPager``.
