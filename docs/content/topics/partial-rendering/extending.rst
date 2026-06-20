.. _topics-partial-rendering-extending:

Extending the Protocol
======================

The protocol is closed by design.
The server authors every verb and the client never invents one, so a page cannot be asked to do anything the server did not name.
Three seams open that protocol to an application without forking the runtime: a custom verb, a server-pushed context value, and a server-fired event.
Each rides the same envelope and the same apply pipeline as the built-ins.

.. contents::
   :local:
   :depth: 1

A Custom Verb
-------------

A verb beyond the built-in set is registered on both sides.
The server registers the name and the client supplies the handler.
A registered name clears the ``next.E066`` check and earns the generic ``op()`` channel on the builder, so the typed methods stay the only authors of the built-in verbs.

Register the name once on the server.

.. code-block:: python
   :caption: dashboard/page.py

   from typing import Any

   from django.http import HttpRequest

   from next.partial import Patches, register_patch_op

   register_patch_op("confetti")


   def done(self, request: HttpRequest, cleaned_data: dict[str, Any]) -> Any:
       """Save the goal and rain confetti on every watching tab."""
       Goal.objects.create(**cleaned_data)
       return Patches(request).op("confetti", count=80).toast("Goal reached").response()

``register_patch_op`` runs at import, so a page module or an app ``ready`` hook is a natural home.
``op(name, **payload)`` emits the verb with an open payload the handler reads on the client.
A payload key may be anything except the structural keys ``op``, ``target``, and ``html``, which raise ``ReservedPatchKeyError``.
An unregistered name raises ``UnknownPatchOpError`` and a built-in name raises ``BuiltinPatchOpError``, so a typo fails loudly rather than reaching a client that cannot handle it.

Supply the handler on the client through a co-located asset.

.. code-block:: javascript
   :caption: static/dashboard/confetti.js

   import { burst } from "./confetti-lib.js";

   Next.partial.defineOp("confetti", (patch, ctx) => {
     burst(patch.count ?? 50);
     ctx.dispatch("confetti", { count: patch.count });
   });

The handler receives the patch and an apply context.
The patch carries the payload fields the server authored, here ``patch.count``.
The context exposes ``dispatch`` for a ``CustomEvent`` on the ``Next.on`` bus, ``mergeContext`` for a context merge, ``root`` for the document, and ``dev`` for the development-build flag.
Registering the handler at load time is safe, because ``defineOp`` records a handler rather than scanning the DOM.

The envelope carries the custom verb beside the built-ins.

.. code-block:: json
   :caption: response body

   {
     "version": "9f3c2e1b",
     "ops": [
       {"op": "confetti", "count": 80},
       {"op": "toast", "text": "Goal reached", "variant": "info"}
     ]
   }

Without the runtime the mutation falls back to the full ``POST`` then ``303`` then ``GET`` cycle and the custom verb never ships, so a custom verb is an enhancement, never the only path to a result.

Pushing Context
---------------

The ``context`` verb merges named values into ``window.Next.context`` and fires ``context-updated``.
A value is pushed by the name of a registered ``serialize=True`` provider on the origin page, so the wire carries plain data the same serializer produced on the full render.

.. code-block:: python
   :caption: cart/page.py

   from typing import Any

   from django.http import HttpRequest

   from next.pages import context
   from next.partial import Patches


   @context("cart_count", serialize=True)
   def cart_count(request: HttpRequest) -> int:
       """Expose the cart size to the client context."""
       return Cart.for_request(request).count


   def done(self, request: HttpRequest, cleaned_data: dict[str, Any]) -> Any:
       """Add the item and push the new cart count to the client."""
       cart = Cart.for_request(request)
       cart.add(cleaned_data["sku"])
       return Patches(request).context(cart_count=cart.count).response()

A name that is not a ``serialize=True`` provider of the origin page raises ``UnknownContextNameError``, so the verb cannot smuggle an arbitrary value past the provider contract.

Read the merged value on the client through ``Next.context`` and react to the merge through ``context-updated``.

.. code-block:: javascript
   :caption: static/cart/badge.js

   Next.on("context-updated", () => {
     document.querySelector("[data-cart-badge]").textContent =
       String(Next.context.cart_count);
   });

A stream source cannot build a ``context`` patch, because it has no page-render origin to read a provider value from.
A stream that needs to push fresh context drives a ``refresh`` instead, and the re-fetched zone delivers the new context through its own render, see :doc:`sse`.

Firing an Event
---------------

The ``event`` verb dispatches a ``CustomEvent`` on the document and the ``Next.on`` bus.
It is the seam for a server-authored signal that no morph expresses, a notification an existing widget already listens for.

.. code-block:: python
   :caption: orders/page.py

   from typing import Any

   from django.http import HttpRequest

   from next.partial import Patches


   def done(self, request: HttpRequest, cleaned_data: dict[str, Any]) -> Any:
       """Place the order and signal the analytics island."""
       order = Order.objects.create(**cleaned_data)
       return (
           Patches(request)
           .event("order-placed", {"id": order.pk, "total": str(order.total)})
           .toast("Order placed", variant="success")
           .response()
       )

Consume it with a delegated document listener or the ``Next.on`` bus.

.. code-block:: javascript
   :caption: static/orders/analytics.js

   document.addEventListener("order-placed", (event) => {
     analytics.track("purchase", event.detail);
   });

The ``toast`` verb is sugar over ``event`` with a built-in container, so a project that wants its own notification surface listens for ``next:toast`` and renders the toast itself.

See Also
--------

.. seealso::

   :doc:`reference` for the verbs, the lifecycle events, and the client runtime surface.
   :doc:`co-located-js` for keeping the handler alive across a morph.
   :doc:`sse` for why a stream pushes ``refresh`` rather than ``context``.
   :doc:`/content/ref/partial` for the Python API of ``register_patch_op`` and ``Patches``.
