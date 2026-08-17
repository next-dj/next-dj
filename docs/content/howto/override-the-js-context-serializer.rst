.. _howto-js-serializer:

Override the JS context serializer
==================================

Problem
-------

The default JSON serializer does not know how to encode Pydantic models or other custom types that you publish through ``@context(serialize=True)``.

Solution
--------

Set ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` to the dotted path of a serializer class, or pass ``serializer=`` on a single ``@context`` decorator.

Walkthrough
-----------

Use the bundled pydantic serializer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The framework ships ``PydanticJsContextSerializer``.
Point the setting at it.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "next.static.PydanticJsContextSerializer",
   }

Context functions can now return Pydantic models directly.

.. code-block:: python
   :caption: notes/pages/page.py

   from pydantic import BaseModel
   from next import context

   class NoteOut(BaseModel):
       id: int
       title: str

   @context("featured", serialize=True)
   def featured() -> NoteOut:
       return NoteOut(id=1, title="Hello")

Write a custom serializer
~~~~~~~~~~~~~~~~~~~~~~~~~

A serializer is any class with a ``dumps`` method that returns a JSON string.
See :doc:`/content/topics/static-assets/js-context` for the protocol and a minimal ``CompactSerializer`` example, then set ``JS_CONTEXT_SERIALIZER`` to your dotted path.

The framework instantiates the class with no arguments, so a serializer that needs configuration reads it from settings or from class attributes rather than from constructor parameters.
``PydanticJsContextSerializer`` shows the shape, its own constructor takes nothing and raises ``ImportError`` when the optional pydantic package is absent.

What the setting accepts
~~~~~~~~~~~~~~~~~~~~~~~~

The value is a dotted path string, not a class object.
The settings merge keeps only a string or ``None`` under this key, so a class object assigned directly is dropped and the default JSON serializer stays in place with no error at import time.

The class is resolved lazily rather than at startup, once per request-scoped collector the first time a page registers a ``serialize=True`` value.
A bad value therefore surfaces at render time instead of at boot, a path that cannot be imported raising ``ImportError`` and a class whose instance has no ``dumps(value) -> str`` method raising ``TypeError``.

Run ``uv run python manage.py check`` before deploying.
``next.W042`` reports a non-string value, an unimportable path, a name that is not a class, a class that cannot be instantiated with no arguments, and a class that does not implement the protocol.

Per key override
~~~~~~~~~~~~~~~~

Pass ``serializer=`` on a single ``@context`` so only that key uses a different encoder.
That argument takes a serializer instance rather than a dotted path, because the decorator runs in Python where the object is already available.
Everything else keeps the project default.
See the Per-key serializer section in :doc:`/content/topics/static-assets/js-context` for a concrete snippet.

Verification
------------

Reload a page and inspect ``window.Next.context`` in the browser console.
The values that could not serialise before now appear as proper JSON.

See also
--------

.. seealso::

   :doc:`/content/topics/static-assets/js-context` for the topic guide.
   :doc:`/content/topics/context` for the ``serialize`` flag.
