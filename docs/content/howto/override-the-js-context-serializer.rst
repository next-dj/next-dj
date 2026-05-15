.. _howto-js-serializer:

Override the JS Context Serializer
==================================

Problem
-------

The default JSON serializer does not know how to encode Pydantic models, ``datetime`` instances, or other non standard types that you publish through ``@context(serialize=True)``.

Solution
--------

Point ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` at a callable that converts the context map before JSON encoding.

Walkthrough
-----------

Write the serializer.

.. code-block:: python
   :caption: notes/serializers.py

   from datetime import datetime

   from pydantic import BaseModel


   def serialize_value(value: object) -> object:
       if isinstance(value, datetime):
           return value.isoformat()
       if isinstance(value, BaseModel):
           return value.model_dump()
       return value


   def json_context(payload: dict[str, object]) -> dict[str, object]:
       return {key: serialize_value(value) for key, value in payload.items()}

Wire it up.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "notes.serializers.json_context",
   }

The framework calls the function once per render and JSON encodes the result.

Per Key Override
~~~~~~~~~~~~~~~~

For per key conversions, pass ``serializer=`` directly on the decorator.

.. code-block:: python
   :caption: per key

   from next.pages import context


   @context("note", serialize=True, serializer=lambda v: v.model_dump())
   def note() -> NoteOut:
       return NoteOut(id=1, title="Hello")

Class Based Serializer
~~~~~~~~~~~~~~~~~~~~~~

Subclass ``JsContextSerializer`` for stateful logic.

.. code-block:: python
   :caption: notes/serializers.py

   from next.static.serializers import JsContextSerializer


   class StrictJsContextSerializer(JsContextSerializer):
       def serialize_value(self, key: str, value: object) -> object:
           if key.startswith("secret_"):
               return None
           return super().serialize_value(key, value)

Point the setting at the class.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "notes.serializers.StrictJsContextSerializer",
   }

Verification
------------

Reload a page and inspect ``window.Next.context`` in the browser console.
The previously failing values now appear as proper JSON.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/js-context` for the topic guide.
   :doc:`/content/topics/context` for the ``serialize`` flag.
