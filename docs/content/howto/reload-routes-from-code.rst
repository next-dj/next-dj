.. _howto-reload-routes:

Reload Routes From Code
=======================

Problem
-------

A custom router backend reads URLs from a database table and you need to rebuild the URL set when a row changes.

Solution
--------

Call ``router_manager.reload()`` from a :doc:`signal receiver <django:topics/signals>` on the relevant Django model.
The framework rebuilds every backend from the current configuration, clears the Django URL resolver cache, and emits a ``router_reloaded`` signal.

Walkthrough
-----------

Wire the receiver.

.. code-block:: python
   :caption: notes/receivers.py

   from django.db.models.signals import post_delete, post_save
   from django.dispatch import receiver
   from next.urls import router_manager
   from notes.models import Note

   @receiver(post_save, sender=Note)
   @receiver(post_delete, sender=Note)
   def reload_router(**_kwargs) -> None:
       router_manager.reload()

Connect the receiver from ``AppConfig.ready`` so it runs at startup.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           from notes import receivers  # noqa: F401, PLC0415

Each call rebuilds the backend list from the current ``NEXT_FRAMEWORK`` configuration, clears Django's URL caches, and emits ``router_reloaded``.
Receivers should tolerate being invoked more than once when several writes batch into one task.

Observe the Reload
------------------

Long lived processes that cache URL references can listen to ``router_reloaded``.

.. code-block:: python
   :caption: cache invalidation

   from django.dispatch import receiver
   from next.urls.signals import router_reloaded

   @receiver(router_reloaded)
   def drop_url_cache(**_kwargs) -> None:
       my_cache.clear()

Verification
------------

Add a row to the underlying table and confirm that the next request resolves the new URL without restarting the server.

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for the reload mechanics.
   :doc:`/content/internals/url-router` for the manager internals.
