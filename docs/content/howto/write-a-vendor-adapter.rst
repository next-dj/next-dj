.. _howto-write-a-vendor-adapter:

Write a vendor adapter
======================

Problem
-------

A site needs Plausible or Google Analytics 4, loaded only once the visitor grants the ``analytics`` category, counting every partial navigation as a page view, and running under a nonce-based Content Security Policy.
The framework ships no vendor integration, only the mechanism one is built from.

Solution
--------

Declare the vendor in ``scripts.py`` as ``Script`` values in the ``analytics`` category, configured through ``data-*`` attributes.
Add a small static adapter that waits for the runtime, sends a page view for the first page and on every ``next:navigated``, and reacts to ``next:consent``.

Walkthrough
-----------

Declare the category
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "CONSENT": {"CATEGORIES": ["necessary", "analytics"]},
   }

Setting ``CONSENT`` turns consent on, and ``analytics`` is the project's own category, since the framework knows only ``necessary``, see :doc:`/content/topics/scripts/consent`.

Wait for the runtime
~~~~~~~~~~~~~~~~~~~~

A head script the server writes with ``ASYNC`` may run before ``window.Next`` exists.
Every adapter below opens with the same helper, which resolves once the runtime is on the page, whatever the strategy.

.. code-block:: javascript
   :caption: the first lines of every adapter

   function runtime() {
     if (window.Next) return Promise.resolve(window.Next);
     return new Promise((resolve) => {
       document.addEventListener("DOMContentLoaded", () => resolve(window.Next), { once: true });
     });
   }

Plausible
~~~~~~~~~

.. code-block:: python
   :caption: shop/pages/scripts.py

   from django.conf import settings

   from next.scripts import Script, Strategy

   PLAUSIBLE_QUEUE = "window.plausible = window.plausible || function () { (window.plausible.q = window.plausible.q || []).push(arguments); };"

   scripts = (
       Script(
           "plausible",
           src="https://plausible.io/js/script.manual.js",
           init=PLAUSIBLE_QUEUE,
           category="analytics",
           strategy=Strategy.DEFER,
           attrs={"data-domain": settings.PLAUSIBLE_DOMAIN},
       ),
       Script("plausible-pageviews", src="shop/analytics/plausible.js", category="analytics", strategy=Strategy.DEFER),
   )

Plausible reads ``data-domain`` from its own tag.
The manual variant, ``script.manual.js``, sends no page view by itself, so the adapter is the one source of page views and a navigation never counts twice.
The ``init`` queue stub lets the adapter call ``plausible`` before the loader has run.

.. code-block:: javascript
   :caption: shop/static/shop/analytics/plausible.js

   async function start() {
     const Next = await runtime();
     const pageview = (url) => window.plausible("pageview", { u: url });
     pageview(Next.navigation.current().url);
     Next.on("next:navigated", ({ url, action }) => {
       if (action !== "none") pageview(url);
     });
   }

   start();

``next:navigated`` does not fire for the page load, so the adapter counts the first page from ``Next.navigation.current()``, and an ``action`` of ``none`` marks a change of the title alone, see :doc:`/content/topics/scripts/page-views`.
Plausible sets no cookie, so a revoke has nothing to clear.

Google Analytics 4
~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: shop/pages/scripts.py

   from django.conf import settings

   from next.scripts import Script, Strategy

   scripts = (
       Script(
           "ga4",
           src="shop/analytics/ga4.js",
           category="analytics",
           strategy=Strategy.DEFER,
           attrs={"data-measurement-id": settings.GA4_ID},
       ),
   )

The one ``Script`` is the adapter, and the adapter inserts ``gtag.js`` itself.
It copies its own nonce onto that loader, since the framework writes the nonce only onto the tags it renders, and a policy without ``'strict-dynamic'`` refuses a script that carries none.

.. code-block:: javascript
   :caption: shop/static/shop/analytics/ga4.js

   const adapter = document.currentScript;
   const id = adapter.dataset.measurementId;

   window.dataLayer = window.dataLayer || [];
   function gtag() {
     window.dataLayer.push(arguments);
   }
   gtag("js", new Date());
   gtag("config", id, { send_page_view: false });

   const loader = document.createElement("script");
   loader.async = true;
   loader.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
   loader.nonce = adapter.nonce;
   document.head.append(loader);

   function pageView({ url, title }) {
     gtag("event", "page_view", { page_location: url, page_title: title });
   }

   async function start() {
     const Next = await runtime();
     pageView(Next.navigation.current());
     Next.on("next:navigated", (detail) => {
       if (detail.action !== "none") pageView(detail);
     });
     const { consent } = await Next.ready("scripts");
     Next.on("next:consent", () => {
       if (!consent.get().analytics) revoke();
     });
   }

   start();

``document.currentScript`` is read at the top, since it is ``null`` once the adapter awaits anything.
``send_page_view: false`` leaves the page views to the adapter.

.. warning::

   GA4 Enhanced Measurement sends its own page view on a browser history change, so beside the adapter every navigation counts twice.
   Turn off "Page changes based on browser history events" under the web stream's Enhanced Measurement settings.

Gating the adapter by ``analytics`` means no Google tag loads before the visitor grants the category, which is what Google calls basic Consent Mode.
Advanced Consent Mode loads the tag before consent instead, so its adapter is declared ``necessary``, calls ``gtag("consent", "default", ...)`` with every storage type denied before ``config``, and sends ``gtag("consent", "update", ...)`` from ``next:consent``.

.. _howto-vendor-adapter-revoke:

Clear the vendor's cookies on revoke
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A revoke keeps the category's waiting scripts from loading, while a script that already runs keeps running until the page goes, and the cookies it set stay.
The adapter clears them from its ``next:consent`` listener, on the host and on every parent domain, since GA4 writes its cookies on the registrable domain.

.. code-block:: javascript
   :caption: shop/static/shop/analytics/ga4.js, continued

   function expire(name) {
     document.cookie = `${name}=; max-age=0; path=/`;
     const labels = location.hostname.split(".");
     for (let i = 0; i < labels.length - 1; i += 1) {
       document.cookie = `${name}=; max-age=0; path=/; domain=.${labels.slice(i).join(".")}`;
     }
   }

   function revoke() {
     gtag("consent", "update", { analytics_storage: "denied" });
     for (const pair of document.cookie.split("; ")) {
       const name = pair.split("=")[0];
       if (name === "_ga" || name.startsWith("_ga_")) expire(name);
     }
   }

A banner that calls ``update(choice, {reload: true})`` reloads the page on a change, which stops the running tag as well.

.. _howto-vendor-adapter-cmp:

Bridge a consent platform
~~~~~~~~~~~~~~~~~~~~~~~~~

A consent management platform owns the banner and its own cookie.
A ``ConsentBackend`` subclass reads that cookie on the server and maps the platform's purposes onto the categories.

.. code-block:: python
   :caption: shop/consent.py

   from django.http import HttpRequest

   from next.consent import NECESSARY, UNDECIDED, Consent, ConsentBackend

   class PlatformConsentBackend(ConsentBackend):
       def read(self, request: HttpRequest) -> Consent:
           raw = request.COOKIES.get(self.options.get("cookie_name", "cmp_consent"))
           if raw is None:
               return UNDECIDED
           purposes = self.options.get("categories", {})
           granted = {purposes[purpose] for purpose in raw.split(",") if purpose in purposes}
           return Consent(frozenset({NECESSARY, *granted}), decided=True)

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "CONSENT": {
           "BACKEND": "shop.consent.PlatformConsentBackend",
           "CATEGORIES": ["necessary", "analytics", "marketing"],
           "OPTIONS": {"cookie_name": "cmp_consent", "categories": {"statistics": "analytics", "ads": "marketing"}},
       },
   }

In the browser a small script mirrors every decision of the platform into ``Next.consent``, so the gated scripts follow it without a reload.

.. code-block:: javascript
   :caption: shop/static/shop/cmp.js

   window.Next.ready("scripts").then(({ consent }) => {
     window.addEventListener("cmp:decided", ({ detail }) => {
       consent.update({ analytics: detail.statistics, marketing: detail.ads });
     });
   });

The event name and its detail are the platform's own.
An ``update`` that repeats the stored choice changes nothing, so a platform that announces its decision on every load costs no cookie write.

Verification
------------

Open a page in a fresh browser session with the network panel recording.
No request reaches ``plausible.io`` or ``googletagmanager.com`` before the banner is answered.
Grant ``analytics``, and the vendor script loads without a reload and reports the current page.
Follow a filter link or open a layer, and one more page view leaves per navigation.
Revoke the category, and the ``_ga`` cookies are gone from the storage panel.

Run ``uv run python manage.py check``, which reports a script in a category ``CATEGORIES`` does not list and an adapter file no staticfiles finder answers.

See also
--------

.. seealso::

   :doc:`/content/topics/scripts/declaring` for ``scripts.py`` and the strategies.
   :doc:`/content/topics/scripts/page-views` for ``next:navigated``.
   :doc:`/content/ref/client-extras` for ``Next.ready("scripts")``, ``Next.consent``, and the events.
   :doc:`/content/security/csp-and-nonce` for the nonce under a Content Security Policy.
