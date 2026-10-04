.. _topics-scripts-consent:

Consent
=======

A script, a block of markup, or a server-side decision can wait for the visitor's consent to a category.
The categories live in ``NEXT_FRAMEWORK["CONSENT"]``, a consent backend reads the visitor's choice on the server, and ``Next.consent`` records it in the browser.
This page covers turning consent on, the categories, the cookie, where gated scripts render, the ``Consent`` parameter, ``{% #consented %}``, and the banner's side in JavaScript.

.. contents::
   :local:
   :depth: 2

Turning consent on
------------------

The ``CONSENT`` key of ``NEXT_FRAMEWORK`` is the switch.
With the key set, every page carries the consent state in the ``$consent`` entry of its init payload, and the runtime fetches the scripts chunk that holds ``Next.consent`` on every page.
Without it, only a page whose tree declares scripts or whose render meets a ``{% #consented %}`` block carries the state.

That difference matters for markup that arrives later.
A partial response carries no consent state, so a ``{% #consented %}`` block that reaches a page only through a patch stays hidden on a page that loaded without it, and ``manage.py check`` warns about such a block while ``CONSENT`` is unset.
An empty mapping is enough to turn consent on with every default in place.

Categories
----------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "CONSENT": {
           "BACKEND": "next.consent.CookieConsentBackend",
           "CATEGORIES": ["necessary", "analytics", "marketing"],
           "SERVER_RENDER": "auto",
           "OPTIONS": {"cookie_name": "next_consent", "max_age": 15552000, "samesite": "Lax"},
       },
   }

The framework knows one category, ``necessary``, which is always granted, since the site does not work without it.
Every other category is the project's own, and it declares the ones its scripts use in ``CATEGORIES``, ``necessary`` among them.
Every category but ``necessary`` stays denied until the visitor chooses.
A category name is a cookie-safe token of letters, digits, ``_``, ``-``, and ``.``, since the cookie writes the granted names between colons, separated by ``|``.
The other values shown are the defaults, and the ``CONSENT`` entry of :doc:`/content/ref/settings` lists the checks that validate the scope.

The cookie
----------

``CookieConsentBackend`` reads a first-party cookie the runtime writes on every choice, ``next_consent=2:analytics|marketing:<seconds>``, the version, the granted categories past ``necessary``, and the time of the decision.
The server and the runtime also read the ``1:analytics,marketing:<seconds>`` form, which separates the categories with commas, and the runtime writes the ``2:`` form on the next choice.
The cookie is not ``HttpOnly``, since the runtime writes it, and no server endpoint is involved.
``OPTIONS`` names the cookie, its ``max_age``, ``samesite``, ``domain``, ``path``, and ``secure``, which follows the scheme of the page while it is ``None``.

A consent platform that owns its own banner and cookie plugs in as another backend, see :ref:`howto-vendor-adapter-cmp`.
The runtime keeps the choice in this cookie alone, whatever the backend.
A backend tells it the cookie through ``client_config()``, the entries it adds to ``$consent``, and one that adds none leaves the runtime writing ``next_consent`` with the default age.

A backend that fails to import, to build, or to read, or that answers anything but a ``Consent``, never fails the page.
Under ``DEBUG`` or ``STRICT_LOADING`` the error is raised with a note naming ``NEXT_FRAMEWORK['CONSENT']['BACKEND']``.
Otherwise every visitor reads as undecided, every category but ``necessary`` stays denied, and the failure is logged at most once every ten minutes.
A backend that fails to import or to build is not tried again until the framework settings reload.

Where gated scripts render
--------------------------

``SERVER_RENDER`` decides whether the server reads the consent cookie while it renders.

``"auto"``
   The server reads the cookie on a page no shared cache holds and writes the allowed head scripts into the HTML, adding ``Vary: Cookie`` when the page has gated content.
   A page whose ``cache`` lets a CDN keep it renders the same HTML for every visitor, sends every gated script through the manifest, and adds no ``Vary: Cookie``.

``True``
   The server always reads the cookie, a shared page included, and a shared page whose HTML then follows the cookie is sent with ``Cache-Control: private`` beside its ``Vary: Cookie``, since many CDNs ignore ``Vary``.

``False``
   The server never reads it, every gated script and block waits for the runtime, and a ``Consent`` parameter receives ``UNDECIDED`` on every page.

Either way the runtime reads the cookie on load and activates what the choice allows, so a returning visitor's tags run without a round trip.
A gated script the server did not write loads once the category is granted, without a reload.

The Consent parameter
---------------------

A parameter annotated ``Consent`` receives the visitor's choice, in a ``@context`` callable, a ``render()``, or an action handler.

.. code-block:: python
   :caption: shop/pages/page.py

   from next import context
   from next.consent import Consent

   @context("consent")
   def visitor_consent(consent: Consent) -> Consent:
       return consent

``consent.allows("marketing")`` answers the question, ``consent.decided`` whether the visitor chose at all, and ``{% if consent.marketing %}`` reads the same in a template.
Where the server reads the cookie, reading it marks the response ``Vary: Cookie``.
Where it does not, on a shared page under ``"auto"`` and on every page under ``False``, the parameter receives ``UNDECIDED``, a visitor who has not chosen, and adds no ``Vary``, so the cached HTML shows no one in particular.
Under ``True`` a shared page reads the real choice and is sent with ``Cache-Control: private``.

Gating markup
-------------

``{% #consented "<category>" %}`` renders its body for a visitor who granted the category, and the optional ``{% else %}`` branch for everyone else.

.. code-block:: jinja
   :caption: shop/pages/lp/[campaign]/template.djx

   {% #consented "marketing" %}
     <iframe src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ" title="Launch video"></iframe>
   {% else %}
     <button type="button" data-grant="marketing">Show the video</button>
   {% /consented %}

A co-located script calls ``Next.consent.update({marketing: true})`` when the button is clicked.
When the server reads consent it renders the one branch that applies.
When the runtime decides, the server writes the body inert inside a ``<template data-next-consented="marketing">``, followed by the else branch and an end marker, and the runtime swaps the branch for the body once the category is granted, running the mount pass over it.
A block a patch brings in is revealed the same way, as long as the page loaded with the consent state.
A revoke leaves revealed markup in place until the next page load.

The body is gated as markup, and the scripts it registers wait for the category as well.
When the runtime decides, the body renders against a collector of its own.
Its stylesheets and the ``serialize=True`` context of its components join the page, since they do nothing until the markup shows.
A ``{% use_script %}``, ``{% use_module %}``, or ``{% #use_script %}`` in the body, a co-located script of a component it renders, and a ``{% script %}`` naming a declared script all become ``$scripts`` entries in the category of the block, so the runtime loads them once the visitor grants it.
They load in order after the page, not where the body stands, and a script the rest of the page registers as well loads with the page.
A gate only tightens.
A declared script keeps its own category and waits for the block's as well, so a ``marketing`` script named inside a ``{% #consented "analytics" %}`` block needs both, and a block nested in another needs the outer category too.
A script that several client-rendered blocks name waits for the category of each of them.
The entry names every category it waits for, separated by a space, and both ``Consent.allows`` and the runtime read such a name as all of them.
Any other asset kind the body registers is dropped and logged at most once every ten minutes, since only a script can wait for the category.
When the server decides, the body renders in place for a visitor who granted the category, its scripts with it.

The banner
----------

The consent banner is the project's own markup, and ``Next.consent`` is the surface it drives.
``Next.consent`` exists once the scripts chunk, ``next.scripts.min.js``, has loaded, so the banner waits for it through ``Next.ready("scripts")``.

.. code-block:: javascript
   :caption: shop/static/shop/banner.js

   const banner = document.querySelector("#consent-banner");

   window.Next.ready("scripts").then(({ consent }) => {
     banner.hidden = consent.decided();
     banner.querySelector("[data-accept]").addEventListener("click", () => {
       consent.acceptAll();
       banner.hidden = true;
     });
     banner.querySelector("[data-reject]").addEventListener("click", () => {
       consent.rejectAll();
       banner.hidden = true;
     });
   });

``Next.ready("scripts")`` resolves once the chunk has taken the page's payload, fetching it on a page that did not, and rejects when the chunk cannot load, so a banner never records a choice nothing keeps.

``update({analytics: true, marketing: false})`` records a choice per category and leaves unnamed ones as they were, and ``{reload: true}`` reloads the page when a category changed, since a revoked script keeps running until the page goes.
An ``update`` that changes nothing for a visitor who already decided writes no cookie and fires no ``next:consent``, so a banner or a consent platform that repeats the choice on every load has no effect.
A revoke keeps every script of the category still waiting on its strategy from loading.
The cookies a vendor already set stay, and a ``next:consent`` listener clears them, see :ref:`howto-vendor-adapter-revoke`.

Every choice fires ``next:consent`` with the granted, denied, and changed categories, and the page fires one with ``initial: true`` once the chunk has taken the payload.
A listener receives only the events that follow its registration, so a script that loads late reads the current state from ``consent.get()`` and listens for the changes after it.

See also
--------

.. seealso::

   :doc:`declaring` for the ``category`` of a script.
   :doc:`/content/howto/write-a-vendor-adapter` for a vendor under consent and a consent platform bridge.
   :doc:`/content/ref/scripts` for ``Consent``, ``ConsentBackend``, and ``get_consent``.
   :doc:`/content/ref/client-extras` for ``Next.ready("scripts")`` and ``Next.consent``.
