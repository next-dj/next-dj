.. _security-csp-and-nonce:

CSP and nonce
=============

A Content Security Policy restricts which scripts a page may run.
The client runtime is designed to live under one, and this page covers how the runtime carries a nonce, why scripts in patches never run, and what ``'strict-dynamic'`` does and does not guarantee.

.. contents::
   :local:
   :depth: 1

The nonce from currentScript
----------------------------

The runtime remembers the nonce of the script that bootstrapped it.
On load it reads ``document.currentScript.nonce`` and copies that value onto every element it injects for a co-located asset delta, script, module, ``<link rel="stylesheet">``, and inline ``<style>`` alike.
A dynamically inserted element carries the page nonce, so a policy that allows nonced scripts and styles allows the assets the runtime loads.

The framework does not write a nonce onto the bootstrap tag or onto the inline ``Next._init`` script.
Both come from ``NextScriptBuilder``, whose templates are formatted once per process and cannot carry a per-request value.
Under a nonce policy switch ``NEXT_JS_OPTIONS`` to the ``MANUAL`` policy and emit the three fragments from a template tag that reads the nonce off the request, as described under :ref:`Runtime script options <topics-static-js-runtime-script-options>`, or admit the two script fragments by hash.

The nonce is the only attribute the runtime carries over from the page.
An element it builds for a patch-inserted asset takes a fixed attribute set, so an ``integrity`` or ``crossorigin`` attribute a backend writes into its tag templates reaches the browser on a full render alone, see :doc:`/content/topics/partial-rendering/limitations`.

Scripts in patches never run
----------------------------

A ``<script>`` inside patch HTML is never executed by any insertion path.
The applier removes every script element from parsed patch HTML before it reaches the document.
This is structural neutralisation, not a parser side effect.
The script is cut out, so there is no element for the browser to evaluate and no nonce question to answer.

The consequence for a CSP is that a morph cannot smuggle an inline script past the policy, because a morph cannot run an inline script at all.
Behaviour arrives only through the co-located asset manifest, whose scripts are nonced, and through the ``event`` verb, which carries no code.
A widget that relied on an inline initialiser in its markup has to move to a co-located module, see :doc:`/content/topics/partial-rendering/co-located-js`.

With the runtime's dev mode on, that is with Django ``DEBUG``, the runtime prints a ``console.warn`` for every script it neutralises.
An inline initialiser that stopped working is therefore visible rather than silent.

What the applier does not remove
--------------------------------

The removal covers ``<script>`` elements and nothing else.
An event-handler attribute such as ``onclick``, an ``<iframe srcdoc>`` carrying a document of its own, and a ``javascript:`` href all reach the live document exactly as the server wrote them, because each one is an attribute rather than an element the applier can cut out.

A project running under a nonce policy without ``'unsafe-inline'`` already has its defence, since the browser refuses all three.
A project running without a CSP has one defence left, the template's own auto escaping, which is what keeps an untrusted value from becoming an attribute in the first place.
The rule follows from that: never build patch HTML by string concatenation around a request value, and never ``mark_safe`` a value the request supplied, see :doc:`di-and-untrusted-input`.
A patch rendered through a zone or a component template carries the same escaping a full page render does.

strict-dynamic as a recommendation
-----------------------------------

``'strict-dynamic'`` lets a script already trusted by a nonce load further scripts without each one needing its own nonce in the policy.
It pairs well with the runtime, because the nonced bootstrap script loads the asset scripts and ``'strict-dynamic'`` propagates that trust to them.

The framework carries the nonce and refuses to run inline patch scripts, and everything past that is your policy to author and verify against your own threat model.
Treat ``'strict-dynamic'`` as a sensible default for a nonce-based policy rather than as a setting the framework validates.

A worked policy
---------------

A nonce-based policy with ``'strict-dynamic'`` looks like this, with ``{nonce}`` filled in per request by your CSP middleware.

.. code-block:: text
   :caption: a Content-Security-Policy header

   Content-Security-Policy: script-src 'nonce-{nonce}' 'strict-dynamic'; object-src 'none'; base-uri 'none'

Under the ``MANUAL`` policy the bootstrap script tag you emit carries ``nonce="{nonce}"``, the runtime copies that nonce onto every asset element it injects, and ``'strict-dynamic'`` lets the bootstrap load the scripts among them.
No patch can introduce an inline script, so no patch needs a nonce of its own.

See also
--------

.. seealso::

   :doc:`/content/topics/partial-rendering/co-located-js` for moving inline initialisers to co-located modules.
   :doc:`/content/security/static-assets` for the origin and integrity of shipped assets.
   :doc:`/content/topics/partial-rendering/sse` for the streaming surface.
