.. _howto-js-serializer:

Override the JS Context Serializer
==================================

Problem
-------

The default JSON serializer does not know how to encode Pydantic models or other custom types that you publish through ``@context(serialize=True)``.

Solution
--------

Point ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` at a serializer class, or pass ``serializer=`` on a single ``@context`` decorator.

Walkthrough
-----------

Use the Bundled Pydantic Serializer
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
   :caption: notes/routes/page.py

   from pydantic import BaseModel
   from next.pages import context

   class NoteOut(BaseModel):
       id: int
       title: str

   @context("featured", serialize=True)
   def featured() -> NoteOut:
       return NoteOut(id=1, title="Hello")

Write a Custom Serializer
~~~~~~~~~~~~~~~~~~~~~~~~~

A serializer is any class with a ``dumps`` method that returns a JSON string.
See :doc:`/content/topics/static-assets/js-context` for the protocol and a minimal ``CompactSerializer`` example, then point ``JS_CONTEXT_SERIALIZER`` at your dotted path.

Per Key Override
~~~~~~~~~~~~~~~~

Pass ``serializer=`` on a single ``@context`` so only that key uses a different encoder. Everything else keeps the project default.
See the **Per Key Serializer** section in :doc:`/content/topics/static-assets/js-context` for a concrete snippet.

Verification
------------

Reload a page and inspect ``window.Next.context`` in the browser console.
The values that could not serialise before now appear as proper JSON.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/js-context` for the topic guide.
   :doc:`/content/topics/context` for the ``serialize`` flag.
