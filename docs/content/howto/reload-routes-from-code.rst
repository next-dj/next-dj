.. _howto-reload-routes:

Reload routes from code
=======================

Problem
-------

A custom page backend reads URLs from a database table and you need to rebuild the URL set when a row changes.

Solution
--------

Call ``route_manager.reload()`` from a :doc:`signal receiver <django:topics/signals>` on the relevant Django model.
The framework rebuilds every backend from the current configuration, clears the Django URL resolver cache, and emits a ``routes_reloaded`` signal.

Walkthrough
-----------

Wire the receivers.
Each receiver is decorated with ``@receiver`` at module top level in ``notes/signals.py``, which the framework imports on startup, so the decorators run once.

.. code-block:: python
   :caption: notes/signals.py

   from django.db.models.signals import post_delete, post_save
   from django.dispatch import receiver
   from notes.models import Note

   from next.urls import route_manager

   @receiver(post_save, sender=Note)
   def reload_router_on_save(**kwargs: object) -> None:
       route_manager.reload()

   @receiver(post_delete, sender=Note)
   def reload_router_on_delete(**kwargs: object) -> None:
       route_manager.reload()

The ``next`` application imports the ``signals.py`` of every installed app at startup, so the receivers need no ``AppConfig.ready`` code, see :ref:`topics-signals`.

Each call rebuilds the backend list from the current ``NEXT_FRAMEWORK`` configuration, clears Django's URL caches, and emits ``routes_reloaded``.
The framework listens to that signal itself, so the routers the development watcher and the staticfiles finder read are rebuilt with the ones the URL resolver serves.
Receivers should tolerate being invoked more than once when several writes batch into one task.

Observe the reload
------------------

Long lived processes that cache URL references can listen to ``routes_reloaded``.

.. code-block:: python
   :caption: cache invalidation

   from django.dispatch import receiver

   from next.urls.signals import routes_reloaded

   @receiver(routes_reloaded)
   def drop_url_cache(**kwargs: object) -> None:
       my_cache.clear()

A receiver that has to rebuild the routers again calls ``route_manager.reload(notify=False)``.
That keyword skips ``page_backend_loaded``, the intercept-table clear, the URL-cache clear, and ``routes_reloaded``, so the receiver does not re-enter itself.

Verification
------------

Add a row to the underlying table and confirm that the next request resolves the new URL without restarting the server.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the reload mechanics.
   :doc:`/content/internals/url-router` for the manager internals.
