.. _howto-deliver-events-durably:

Deliver events durably
======================

Problem
-------

A realtime publication is best-effort.
The broker delivers a message at most once, a full subscriber queue drops its oldest message, and a publication after ``on_commit`` is lost when the broker is unreachable at that moment.
Some events must not vanish, such as "your export is ready" or a payment confirmation that a page waits for.

Solution
--------

Write the event to a table in the same transaction as the change it announces, and let a worker publish it.
The row commits or rolls back together with the data, so an event exists exactly when its change does.
The worker keeps trying until the broker accepts the message, and the realtime layer stays what it is, a fast fan-out of hints.
The framework ships no outbox, so the table and the worker belong to the project.

The model holds the destination, the event, and the time of its publication.

.. code-block:: python
   :caption: outbox/models.py

   from django.db import models

   class OutboxEvent(models.Model):
       topic = models.CharField(max_length=200)
       name = models.CharField(max_length=100)
       detail = models.JSONField(default=dict)
       created_at = models.DateTimeField(auto_now_add=True)
       published_at = models.DateTimeField(null=True, db_index=True)

       class Meta:
           ordering = ["id"]

Code that changes state records the event inside the same ``atomic`` block.

.. code-block:: python
   :caption: exports/services.py

   from django.db import transaction
   from exports.models import Export
   from outbox.models import OutboxEvent

   def finish_export(export: Export, url: str) -> None:
       with transaction.atomic():
           export.url = url
           export.save(update_fields=["url"])
           OutboxEvent.objects.create(
               topic=f"user:{export.user_id}",
               name="export:ready",
               detail={"export": export.pk, "url": url},
           )

The worker is a management command.
It locks a batch of unpublished rows, publishes them in order, and marks them in one transaction.
``select_for_update(skip_locked=True)`` lets several workers share the table without taking the same row, and it needs PostgreSQL, MySQL 8, or Oracle.

.. code-block:: python
   :caption: outbox/management/commands/publish_outbox.py

   import time

   from django.core.management.base import BaseCommand
   from django.db import transaction
   from django.utils import timezone
   from outbox.models import OutboxEvent

   from next.partial import Patches
   from next.realtime import publish

   def publish_batch(size: int) -> int:
       with transaction.atomic():
           pending = list(
               OutboxEvent.objects.select_for_update(skip_locked=True)
               .filter(published_at__isnull=True)
               .order_by("id")[:size]
           )
           for event in pending:
               detail = {**event.detail, "id": event.pk}
               patches = Patches(None).event(event.name, detail)
               publish(event.topic, patches, on_commit=False)
               event.published_at = timezone.now()
           OutboxEvent.objects.bulk_update(pending, ["published_at"])
       return len(pending)

   class Command(BaseCommand):
       help = "Publish pending outbox events to their realtime topics."

       def add_arguments(self, parser) -> None:
           parser.add_argument("--batch", type=int, default=100)
           parser.add_argument("--interval", type=float, default=0.5)

       def handle(self, *args, **options) -> None:
           while True:
               if publish_batch(options["batch"]) == 0:
                   time.sleep(options["interval"])

Run it as its own process next to the web workers with ``python manage.py publish_outbox``.
The worker publishes with ``on_commit=False``, since its own transaction is the one that matters, and a broker error rolls the batch back so the rows stay pending.
Configure the same shared broker as the web workers, see :doc:`fan-out-across-processes-with-redis`.
A worker on the default ``MemoryBroker`` publishes into its own process and reaches no browser.

Delivery semantics
~~~~~~~~~~~~~~~~~~

The outbox gives at-least-once delivery to the broker.
A crash between ``publish`` and the commit of the batch publishes those events again on the next run, which is why the detail carries the ``id`` of the row.
A page that must act once remembers the ids it handled and ignores a repeat.

Order holds per worker.
Rows publish in ``id`` order, so one worker preserves the order of every topic.
Several workers with ``skip_locked`` take different rows concurrently and may publish a later event before an earlier one, so run one worker or give each worker a fixed share of the topics.

The outbox covers the step from the database to the broker.
The step from the broker to a browser stays best-effort.
A tab that is closed, offline, or slow when the event goes out does not receive it, and the replay log only recovers a short gap, bounded by ``REPLAY['SIZE']`` and ``REPLAY['TTL']``, and is gone after a broker restart.
Replay is a catch-up, not a record, so a page that depends on an event reads the outcome from the database when it loads.
The event then says that state changed, and the page fetches the current state through a guarded request, as in :doc:`stream-live-updates-with-sse`.

Housekeeping
~~~~~~~~~~~~

Published rows pile up, so a periodic job deletes the rows whose ``published_at`` is older than the retention the project wants.
The index on ``published_at`` keeps the lookup of pending rows cheap.

The ``manage.py realtime`` command inspects the result.
``manage.py realtime positions user:7`` shows the position of the last message on a topic, and ``manage.py realtime replay user:7 --after <epoch>:<seq>`` prints what the log holds past a cursor, see :doc:`/content/ref/management`.
