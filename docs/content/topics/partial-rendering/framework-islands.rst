.. _topics-partial-rendering-framework-islands:

Framework Islands
=================

A framework island is a Vue or React root mounted into one element of an otherwise server-rendered page.
The island owns its own subtree, the server owns everything around it, and a partial update has to mount the island when its element appears and unmount it when that element leaves.
An island that mounts but never unmounts leaks its listeners, timers, and observers every time a morph removes its host.

The runtime ships the two events and the one attribute an adapter needs.
It does not ship a compiled Vue or React adapter, because a plugin that imports a framework cannot live in the page-wide bundle and would pull a framework release into the support matrix.
The six lines below are the adapter.

.. contents::
   :local:
   :depth: 1

The Mount and Unmount Pair
--------------------------

``next:mounted`` fires on every node a patch touches, after the operations apply.
``next:removed`` fires on a node immediately before it detaches, for every detach the runtime performs.
Both bubble to the document, so one delegated listener catches every island, and the pair brackets the life of a node inside the document.

Mount through :ref:`Next.partial.onMount <topics-partial-rendering-co-located-js>`, which
runs its callback over the matching elements present at load and over every matching
element a later patch inserts, descendants included.
Unmount through ``next:removed``, walking the detached subtree for islands, because the event fires on the detached root rather than on each descendant.

A React Island
--------------

.. code-block:: javascript
   :caption: a React root that mounts on appear and unmounts on remove

   import { createRoot } from "react-dom/client";
   import { Chart } from "./Chart.js";

   const roots = new WeakMap();

   Next.partial.onMount("[data-chart]", (el) => {
     const root = createRoot(el);
     root.render(Chart(JSON.parse(el.dataset.props ?? "{}")));
     roots.set(el, root);
   });

   document.addEventListener("next:removed", (event) => {
     const node = event.target;
     if (!(node instanceof Element)) return;
     const islands = node.matches("[data-chart]")
       ? [node]
       : node.querySelectorAll("[data-chart]");
     for (const el of islands) {
       const root = roots.get(el);
       if (root !== undefined) {
         root.unmount();
         roots.delete(el);
       }
     }
   });

The ``next:removed`` handler walks the subtree because a morph that removes the zone containing the chart fires the event on the zone, not on the chart inside it.
Matching the node itself and its descendants covers both the island-as-root and the island-inside-a-removed-zone cases.

A Vue Island
------------

.. code-block:: javascript
   :caption: the same shape with a Vue application

   import { createApp } from "vue";
   import Chart from "./Chart.js";

   const apps = new WeakMap();

   Next.partial.onMount("[data-chart]", (el) => {
     const app = createApp(Chart, { ...el.dataset });
     app.mount(el);
     apps.set(el, app);
   });

   document.addEventListener("next:removed", (event) => {
     const node = event.target;
     if (!(node instanceof Element)) return;
     const islands = node.matches("[data-chart]")
       ? [node]
       : node.querySelectorAll("[data-chart]");
     for (const el of islands) {
       const app = apps.get(el);
       if (app !== undefined) {
         app.unmount();
         apps.delete(el);
       }
     }
   });

Preserving the Island Through a Morph
-------------------------------------

A morph of the surrounding zone walks into the island and reconciles its DOM against the server markup, which fights the framework for ownership of that subtree.
Mark the island root ``data-next-keep`` so the morph leaves it untouched.
A keep node needs no id.
With an id the child walk pairs it by hard match, without one it pairs by position, so a framework root the server renders with no stable id is preserved all the same.

.. code-block:: html

   <div data-chart data-next-keep></div>

For a node the runtime should reconcile in some cases and not others, the per-node ``next:morph-element`` event carries a veto.
A listener that calls ``preventDefault`` on it skips the morph of that node and its subtree, which lets a framework apply its own diff while the runtime stays out of the way.

Two Kinds of Atomicity
----------------------

The runtime treats two markers as morph boundaries, and they differ.

A ``data-next-keep`` node is fully opaque.
The morph syncs no attributes and walks none of its children, so a framework that owns both the attributes and the subtree keeps total control.

A custom element, a tag with a hyphen, and any node carrying a shadow root are atomic in a narrower sense.
The morph still syncs their attributes, then stops at the boundary and never enters the children or the shadow tree.
The attribute sync is the point: a server re-render that ships a stale attribute on a custom element overwrites the live one the framework was managing.

Use ``data-next-keep`` for an island whose attributes the framework drives after mount.
Lean on the built-in custom-element atomicity only when the server attributes and the framework attributes never disagree, for example a web component the server seeds once and never re-authors.

The Island Needs Stable Identity
--------------------------------

An island must carry a stable ``data-next-key`` or ``id``, or ``data-next-keep``, on its own root.
Without one the morph pairs it by position, and a keyless re-sort of its siblings reuses a different node for it.

The cost is a silent lost mount.
A morph that drops the old island node fires ``next:removed`` on it, so the unmount runs, but the node the morph inserts in another position is not in the touched set, so ``next:mounted`` never fires for it and the mount never runs.
The island unmounts and never comes back.
A stable key pins the island to its data so the morph reuses the same node, and ``data-next-keep`` pins it by leaving it untouched.

See Also
--------

.. seealso::

   :doc:`co-located-js` for the simpler idioms a widget without a framework root uses.
   :doc:`reference` for the lifecycle events and the client runtime surface in tables.
