.. _topics-partial-rendering-extending:

Extending the protocol
======================

The protocol is closed by design.
The server authors every verb and the client never invents one, so a page cannot be asked to do anything the server did not name.
Three seams open that protocol to an application without forking the runtime.
A custom verb, a server-pushed context value, and a server-fired event each ride the same envelope and the same apply pipeline as the built-ins.

.. contents::
   :local:
   :depth: 1

A custom verb
-------------

A verb beyond the built-in set is registered on both sides.
The server registers the name and the client supplies the handler.
A registered name earns the generic ``op()`` channel on the builder, so the typed methods stay the only authors of the built-in verbs.
The ``next.E066`` check validates the registered names at ``manage.py check``, and an unregistered verb fails at runtime with ``UnknownPatchOpError``.

Register the name once on the server.

.. code-block:: python
   :caption: dashboard/page.py

   from dashboard.forms import GoalForm
   from django.http import HttpRequest, HttpResponse

   from next import action
   from next.forms.markers import DForm
   from next.partial import Patches, register_patch_op

   register_patch_op("confetti")


   @action("save_goal", form_class=GoalForm)
   def save_goal(request: HttpRequest, form: DForm[GoalForm]) -> HttpResponse:
       """Save the goal and rain confetti on every watching tab."""
       form.save()
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
The context exposes ``dispatch`` for an event on the ``Next.on`` bus, ``mergeContext`` for a context merge, and ``root`` for the document.
It also exposes ``dev`` for the runtime's dev mode, which follows Django ``DEBUG`` and is described in the Client runtime section of :doc:`reference`.
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

Pushing context
---------------

The ``context`` verb merges named values into ``window.Next.context`` and fires ``context-updated``.
A value is pushed by the name of a registered ``serialize=True`` provider on the origin page, so the wire carries plain data the same serializer produced on the full render.

.. code-block:: python
   :caption: cart/page.py

   from cart.models import Cart
   from django.http import HttpRequest, HttpResponse

   from next import action, context
   from next.partial import Patches


   @context("cart_count", serialize=True)
   def cart_count(request: HttpRequest) -> int:
       """Expose the cart size to the client context."""
       return Cart.for_request(request).count


   @action("add_to_cart")
   def add_to_cart(request: HttpRequest) -> HttpResponse:
       """Add the item and push the new cart count to the client."""
       cart = Cart.for_request(request)
       cart.add(request.POST["sku"])
       return Patches(request).context(cart_count=cart.count).response()

A name that is not a ``serialize=True`` provider of the origin page raises ``UnknownContextNameError``, so the verb cannot smuggle an arbitrary value past the provider contract.
The ``$csrf`` and ``$dev`` keys of the init payload raise ``ReservedContextKeyError`` whether or not the origin page registered them, symmetric to ``event()`` refusing a framework-owned event name.
The ``$`` namespace therefore stays the framework's on a patch as it is on a full render.
A page that registers either name loses that value on the full render too, so no patch has anything to update, see :doc:`/content/topics/static-assets/js-context`.

Read the merged value on the client through ``Next.context`` and react to the merge through ``context-updated``.
The event payload carries the whole merged store in ``context`` and the keys of the delta in ``changed``, so a listener filters on ``changed`` instead of re-reading every value.

.. code-block:: javascript
   :caption: static/cart/badge.js

   Next.on("context-updated", ({ context, changed }) => {
     if (!changed.includes("cart_count")) return;
     document.querySelector("[data-cart-badge]").textContent =
       String(context.cart_count);
   });

A stream source cannot build a ``context`` patch, because it has no page-render origin to read a provider value from.
A stream that needs to push fresh context drives a ``refresh`` instead, and the re-fetched zone delivers the new context through its own render, see :doc:`sse`.

Firing an event
---------------

The ``event`` verb dispatches a ``CustomEvent`` on the document and the ``Next.on`` bus.
It is the seam for a server-authored signal that no morph expresses, a notification an existing widget already listens for.

.. code-block:: python
   :caption: orders/page.py

   from django.http import HttpRequest, HttpResponse
   from orders.forms import OrderForm

   from next import action
   from next.forms.markers import DForm
   from next.partial import Patches


   @action("place_order", form_class=OrderForm)
   def place_order(request: HttpRequest, form: DForm[OrderForm]) -> HttpResponse:
       """Place the order and signal the analytics island."""
       order = form.save()
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
The item still lands in the built-in ``[data-next-toasts]`` tray, so such a project also hides the tray with CSS.

One active backend
------------------

The three seams above extend the envelope from inside.
The wire format itself is replaced rather than extended, and the replacement lives in the protocol backend.
``PARTIAL_BACKENDS`` holds the protocol backends and only the first entry is active.
The rest are ignored, so multi-backend selection is not a supported seam.
A configuration with more than one entry earns the ``next.W071`` warning at ``manage.py check``.
An application that needs a different envelope shape subclasses ``PartialProtocolBackend``, serialises its own wire format, and makes the subclass the single entry of ``PARTIAL_BACKENDS``.
See :doc:`/content/ref/partial` for the ``PartialProtocolBackend`` API.

See also
--------

.. seealso::

   :doc:`reference` for the verbs, the lifecycle events, and the client runtime surface.
   :doc:`co-located-js` for keeping the handler alive across a morph.
   :doc:`sse` for why a stream pushes ``refresh`` rather than ``context``.
   :doc:`/content/ref/partial` for the Python API of ``register_patch_op`` and ``Patches``.
