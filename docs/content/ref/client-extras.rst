.. _ref-client-extras:

Client consent, scripts, and navigation reference
=================================================

Module summary
--------------

This page records the parts of ``window.Next`` that serve third-party scripts, consent, and navigation, next to :doc:`client`, which records the partial surface and the attribute contract.
``Next.navigation`` lives in the core bundle, ``next.min.js``.
``Next.consent`` and ``Next.scripts`` live in the scripts chunk, ``next.scripts.min.js``, which the runtime fetches only when the init payload carries ``$scripts`` or ``$consent``, or when page code calls ``Next.ready("scripts")``.
Both stay ``undefined`` until the chunk lands, and nothing answers for them before it does.

Public API
----------

Next.ready("scripts")
~~~~~~~~~~~~~~~~~~~~~

``Next.ready("scripts")`` returns a ``Promise<ScriptsChunk>`` that resolves with ``{consent, scripts}`` once the chunk has landed and taken the page's init payload.
It fetches the chunk on a page that did not, and a call made before the init payload arrives waits for it.
It rejects when the chunk cannot load or has not landed within 15 seconds, and a failed fetch also fires ``partial:error`` of kind ``asset``, so a banner never records a choice nothing keeps.
A later call after a failure fetches the chunk again.
The chunk takes the latest payload ``Next._init`` received, even one that arrived while it was still loading.

.. code-block:: javascript
   :caption: static/site/banner.js

   window.Next.ready("scripts").then(({ consent }) => {
     document.querySelector("#consent-banner").hidden = consent.decided();
   });

Next.consent
~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 44 22 34

   * - Member
     - Returns
     - Description
   * - ``get()``
     - ``Readonly<Record<string, boolean>>``
     - Every category with whether it is granted, from the stored decision in the consent cookie, else from the ``$consent`` payload.
   * - ``decided()``
     - ``boolean``
     - Whether the visitor has chosen, on this page or through the consent cookie.
   * - ``update(choice, {reload}?)``
     - ``void``
     - Grant or deny the named categories, keep the others, write the cookie, reveal the consented markup, and fire ``next:consent``.
       ``reload: true`` reloads the page when a category changed.
       A call that changes nothing for a visitor who already decided is a no-op, with no cookie write and no event, so a banner that re-affirms the decision on every load moves nothing.
   * - ``acceptAll()``, ``rejectAll()``
     - ``void``
     - ``update`` with every category granted or denied.

The cookie the runtime writes is described by the ``cookie`` entry of ``$consent``, its name, ``max_age``, ``samesite``, ``domain``, ``path``, and ``secure``, which follows the page scheme when ``null``.
A backend whose ``client_config()`` adds no ``cookie`` entry leaves the runtime writing ``next_consent`` with the default age.
The runtime writes ``2:<a>|<b>:<seconds>`` and reads that and the ``1:<a>,<b>:<seconds>`` format earlier runtimes wrote.

A revoke stops the manifest scripts still waiting on their strategy, which move to ``blocked`` and reject any ``load()`` waiting on them, and a later grant schedules them again.
A script that already runs keeps running, and the cookies it set stay until a ``next:consent`` listener clears them, see :doc:`/content/howto/write-a-vendor-adapter`.

Next.scripts
~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 26 44

   * - Member
     - Returns
     - Description
   * - ``load(name)``
     - ``Promise<void>``
     - Load a script of the page's manifest now, whatever its strategy, resolving once it loaded and rejecting on an error or a denied category.
   * - ``status(name)``
     - ``ScriptStatus | undefined``
     - ``rendered`` for a tag the server wrote, then ``pending``, ``loading``, ``loaded``, ``error``, or ``blocked`` for a manifest entry, and ``undefined`` for an unknown name.

The runtime inserts a manifest entry once its strategy fires and its category is granted, the ``init`` body first and the ``src`` after it, each carrying the entry's nonce or the bootstrap nonce.
Entries arrive in the order ``scripts.py`` declares them, and a ``BLOCKING`` or ``DEFER`` entry keeps that order among the entries inserted with it.

Next.navigation
~~~~~~~~~~~~~~~

``Next.navigation.current()`` answers ``{url, path, title}``, where the page stands now.
It is the one public navigation handle, and ``Next.partial`` carries no ``navigation`` member.

Next.partial.mount
~~~~~~~~~~~~~~~~~~

``Next.partial.mount(nodes)`` runs the mount pass, the ``onMount`` callbacks and the ``next:mounted`` event, over elements inserted outside an envelope, such as a revealed consent block or markup a widget builds.

Events
------

Each event fires on the document as a ``CustomEvent`` and on the ``Next.on`` bus with the same payload.
None of them is replayed, so a listener receives only the events that follow its registration.

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Event
     - Payload
   * - ``next:navigated``
     - ``{url, path, title, action}``, where ``action`` is ``push``, ``replace``, ``pop``, or ``none``.
       It fires once per commit that changes the address or the title, after the history write, the head sync, the asset loads, and the mount pass.
       A change of the title alone fires with ``action: "none"``, and the page load fires nothing.
   * - ``next:consent``
     - ``{granted, denied, changed, initial}``, category lists.
       ``initial: true`` marks the state the page starts from, fired once after the chunk takes the payload.
       A script that subscribes later reads the current state from ``consent.get()``.
   * - ``next:script-loaded``
     - ``{name}``, after a manifest script loaded.
   * - ``next:script-error``
     - ``{name, url}``, after a manifest script failed to load.

``ready`` and ``context-updated`` of :doc:`client` are the only runtime events that reach the bus alone.
The chunk reveals a consented block a patch brings in from the ``nodes`` of ``partial:applied``, the elements that patch touched.

Counting page views
~~~~~~~~~~~~~~~~~~~

A vendor adapter counts the page it loads on from ``Next.navigation.current()``, and one more view per ``next:navigated`` whose ``action`` is not ``none``.

.. code-block:: javascript
   :caption: static/site/vendor.js

   const Next = window.Next;

   window.vendor("page", Next.navigation.current().url);
   Next.on("next:navigated", ({ url, action }) => {
     if (action !== "none") window.vendor("page", url);
   });

:doc:`/content/topics/scripts/page-views` covers layers and the vendor tags that watch history themselves.

The init payload
----------------

The framework reserves three keys of the ``Next._init`` payload for this surface, beside ``$csrf`` and ``$dev``.

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Key
     - Value
   * - ``$chunks``
     - ``{scripts, sse, csrf, poll}``, the URLs of ``next.scripts.min.js``, ``next.sse.min.js``, ``next.csrf.min.js``, and ``next.poll.min.js`` through the staticfiles storage, in every payload, and under ``DEBUG`` ``dev`` too, the URL of ``next.dev.min.js``.
   * - ``$scripts``
     - The manifest, one ``{name, src?, init?, strategy, category, attrs, nonce?}`` per script the server did not write, in declaration order.
   * - ``$consent``
     - ``{categories, decided, granted, cookie?}`` and whatever else the backend's ``client_config()`` adds, where ``cookie`` comes from ``CookieConsentBackend``.

A partial response carries neither ``$scripts`` nor ``$consent``, so the chunk loads from the full page alone.
A project key of one of these names is dropped from the automatic payload, the same as ``$csrf``, and a system check reports it.

Exported types
--------------

.. code-block:: typescript
   :caption: the types next/client/next.ts exports for this surface

   export type NavigationAction = "push" | "replace" | "pop" | "none";

   export interface NavigationState {
     url: string;
     path: string;
     title: string;
   }

   export interface NavigatedDetail extends NavigationState {
     action: NavigationAction;
   }

   export interface ConsentChange {
     granted: string[];
     denied: string[];
     changed: string[];
     initial: boolean;
   }

   export interface ScriptsChunk {
     consent: Pick<Consent, "get" | "decided" | "update" | "acceptAll" | "rejectAll">;
     scripts: Pick<Scripts, "load" | "status">;
   }

   export interface NextChunks {
     scripts: ScriptsChunk;
   }

   export type ScriptStatus = "rendered" | "pending" | "loading" | "loaded" | "error" | "blocked";

See also
--------

.. seealso::

   :doc:`client` for the facade, the partial surface, and the module graph.
   :doc:`/content/topics/scripts/index` for the guides.
   :doc:`/content/howto/write-a-vendor-adapter` for an adapter built on these events.
   :doc:`scripts` for the server side.
