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
``next:removed`` fires on a node just before it detaches, for every detach the runtime performs.
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

See Also
--------

.. seealso::

   :doc:`co-located-js` for the simpler idioms a widget without a framework root uses.
   :doc:`reference` for the lifecycle events and the client runtime surface in tables.
