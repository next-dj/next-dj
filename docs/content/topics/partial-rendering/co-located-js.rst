.. _topics-partial-rendering-co-located-js:

Co-located JavaScript under partial updates
===========================================

A morph replaces DOM nodes.
A node that arrives in a patch was not in the document when the page loaded, and a node that a morph removes takes any listener bound to it with it.
Co-located JavaScript has to survive that, and the rule is to attach behaviour to something that outlives the morph rather than to the markup itself.

Each asset runs exactly once per page lifetime, a URL deduped by its address resolved against the document base, query included and fragment ignored, and an inline body by its content.
The registry behind that promise is built from the document while it parses.
An asset a third party inserts after parsing ends stays outside it, so a later manifest naming the same URL or the same inline body inserts it a second time.
A module loaded for the first zone that needs it is not re-run when a later patch brings more of the same markup.
A module that wires itself up at load time finds the markup that exists at load time and nothing later.
Use one of the three idioms below.

.. contents::
   :local:
   :depth: 1

Delegate on the document
------------------------

Bind one listener on ``document`` and match the target inside the handler.
A delegated listener survives every morph and insertion for free, because the document is never replaced.

.. code-block:: javascript
   :caption: a delegated listener

   document.addEventListener("click", (event) => {
     const trigger = event.target.closest("[data-open-dialog]");
     if (trigger) openDialog(trigger);
   });

This is the right idiom for click, input, and change handlers on elements that come and go.
The shared dialog component in the examples uses it, and it is the pattern that outlasts a morph with no extra work.

Listen for next:mounted
-----------------------

The runtime fires ``next:mounted`` on every node it touches, after the operations apply and the JavaScript delta loads.
Subscribe to it for initialisation that needs a specific element the moment it lands.

.. code-block:: javascript
   :caption: per-element mount

   document.addEventListener("next:mounted", (event) => {
     const node = event.target;
     if (node.matches("[data-chart]")) renderChart(node);
   });

The event bubbles, so a single document listener that checks ``event.target`` catches every mounted node.
This is the idiom for behaviour that owns one element and needs to run when that element appears.

``next:mounted`` fires on the touched nodes of a patch, the roots an op replaces or morphs and the rows a merge brings, not on every descendant of a morphed zone.
A deeply nested element that the morph reconciles in place is not a touched node.
``event.target`` is the touched root above it, so ``event.target.matches("[data-chart]")`` never matches the inner element.
For an element that lands deep inside a morphed subtree reach for ``Next.partial.onMount``, which scans the inserted subtree with ``querySelectorAll`` and runs for every match, descendants included.

The ``next:mounted`` event lives only on ``document.addEventListener``.
It never reaches the ``Next.on`` bus, so ``Next.on("next:mounted")`` is a silent no-op, as are ``Next.on("next:removed")``, ``Next.on("next:morph-element")``, and ``Next.on("next:morph-attribute")``.

Register with Next.partial.onMount
----------------------------------

``Next.partial.onMount(selector, callback)`` is a re-executable registry.
The callback runs over the document on ``ready`` and over every inserted subtree after each apply.
A callback registered after ``ready`` runs at once over the current document, so a co-located script that loads after the runtime still binds the markup already present.
It is the one-for-one replacement of a ``DOMContentLoaded`` scan.

.. code-block:: javascript
   :caption: a mount registry callback

   Next.partial.onMount("[data-markdown-preview]", (el) => {
     attachPreview(el);
   });

The callback fires for the matching elements present at load and for every matching element that a later patch brings.
The callback also re-runs for a matching element the morph reconciled in place, so it must be idempotent per element.
Guard the setup with a data flag or a ``WeakSet`` so a second run over the same element does nothing.
Reach for this when a module wired itself on ``DOMContentLoaded`` before and needs the same per-element setup to keep working under partial updates.

The anti-pattern
----------------

Scanning the DOM at module load is the trap.

.. code-block:: javascript
   :caption: do not do this

   // Runs once at module load, finds nothing the morph brings later.
   for (const toggle of document.querySelectorAll("[data-bulk-toggle]")) {
     wireToggle(toggle);
   }

A module-load scan finds zero nodes when its markup arrives in a patch after the module loaded, and it dies silently.
Mounting on ``DOMContentLoaded`` is the same trap, because the event has already fired by the time a lazy zone or a layer brings the markup.

The second open of a modal is worse.
The asset URL is already in the runtime's loaded registry from the first open, so the module is not re-executed at all, and a scan inside it never runs a second time.

Migrate a module-load scan to one of the three idioms above.
The search catalogue's minimum-length hint and the wiki's markdown preview both register through ``onMount`` for exactly this reason.
With the runtime's dev mode on, that is with Django ``DEBUG``, the runtime prints a ``console.warn`` for every ``<script>`` it neutralises out of a patch.
That surfaces a widget which died silently rather than letting it fail in quiet.

Scripts in patches never run
----------------------------

A ``<script>`` inside patch HTML is never executed by any insertion path.
The applier removes every script element from parsed patch HTML before it reaches the document.
Behaviour is delivered only through the co-located asset manifest and the ``event`` verb, never through inline script in a morph.
This is structural, not a parser side effect, and it is why an inline widget initialiser has to move to one of the idioms above.
See :doc:`/content/security/csp-and-nonce` for how this interacts with a Content Security Policy.

What the applier neutralises is the script inside the patch markup itself.
The zone's co-located assets travel separately, in the envelope's asset manifest, and those do load.
A stylesheet, a classic script, or a module URL the page has not seen is inserted, each once per page lifetime.
An inline body it has not seen is inserted the same way when its kind wraps it in the element the verb builds.
The runtime inserts an asset by the verb the server derived from the kind's renderer.
A custom kind registered with ``render_link_tag``, ``render_script_tag``, or ``render_module_tag`` therefore arrives the same way the built-in kinds do.
A kind registered with a custom renderer carries no verb and is skipped, which the ``next.W074`` check reports at ``manage.py check``.
Behaviour co-located with a zone therefore arrives on a standalone zone render.
It must still use the three idioms above because of the once-per-page execution, not because a delivery channel is missing.

See also
--------

.. seealso::

   :doc:`/content/security/csp-and-nonce` for serving the runtime under a CSP.
   :doc:`/content/topics/static-assets/index` for how co-located assets are discovered and bundled.
   :doc:`/content/topics/static-assets/asset-kinds` for which kinds load through a patch envelope.
   :doc:`reference` for the lifecycle events the runtime fires.
