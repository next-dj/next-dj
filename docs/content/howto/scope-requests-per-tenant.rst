.. _howto-scope-requests-per-tenant:

Scope requests per tenant
=========================

Problem
-------

Several tenants share one Django project, one page tree, and one static pipeline, and every request must see only the data, theme, and asset URLs of the tenant it belongs to.

Solution
--------

Resolve the tenant once in :doc:`middleware <django:topics/http/middleware>` and stash it on the request.
The ``scope`` dependency hands it to ``visible_to`` on every tenant-owned model, so pages and forms load rows through ``DObject`` and see only the rows of that tenant.
A :doc:`context processor <django:ref/templates/api>` and a custom static backend read the tenant back from the request as well.

.. warning::

   A request header is attacker-controlled.
   Any visitor can send an ``X-Tenant`` header of their choosing with curl, so middleware that trusts that header on its own hands one tenant's rows to anybody who guesses another tenant's slug.
   Derive the tenant from the authenticated user's membership or from the request host, and read it from a header only behind a trusted proxy that sets the header itself and strips every inbound copy.

The ``examples/multi-tenant/`` project in the repository reads the tenant from a header, so it belongs to the proxy-fronted case below and needs that proxy in front of it.
See :doc:`/content/misc/examples`.

Walkthrough
-----------

Pick a tenant source the client cannot choose
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The middleware decides which tenant a request belongs to, so its input decides whether the project has isolation at all.
Three sources carry different amounts of trust.

- The membership rows that join users to tenants, which the database owns and no request can rewrite.
- The request host, which ``ALLOWED_HOSTS`` narrows to the names the project publishes.
- A request header, which is evidence only while a proxy in front of the application owns it.

Whichever source the project picks, every error response carries a fixed body.
A body that quotes the submitted slug reflects client input back into the page and turns the endpoint into an oracle for enumerating tenant names.

The tenant model and the membership table carry the mapping.

.. code-block:: python
   :caption: notes/models.py

   from django.conf import settings
   from django.db import models

   class Tenant(models.Model):
       slug = models.SlugField(unique=True)
       subdomain = models.SlugField(unique=True)
       primary_color = models.CharField(max_length=7)

   class Membership(models.Model):
       user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
       tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)

       class Meta:
           constraints = [
               models.UniqueConstraint(fields=["user", "tenant"], name="one_membership"),
           ]

Resolve the tenant from the user's membership
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The middleware reads the active tenant out of the membership rows of the signed-in user.
The request carries no tenant identifier, so there is nothing to forge.
A user who belongs to several tenants picks one and the choice lives in the session, yet the lookup still starts from the membership rows, so a tampered session value matches no row and the request falls back to the first membership.

.. code-block:: python
   :caption: notes/middleware.py

   from django.http import HttpResponse
   from notes.models import Membership

   def _active_membership(request):
       """Return the membership the request acts under, or `None`."""
       memberships = Membership.objects.filter(user=request.user).select_related("tenant")
       chosen = request.session.get("tenant_id")
       if chosen is not None:
           return memberships.filter(tenant_id=chosen).first()
       return memberships.first()

   class TenantMiddleware:
       def __init__(self, get_response):
           self._get_response = get_response

       def __call__(self, request):
           request.tenant = None
           user = getattr(request, "user", None)
           if user is not None and user.is_authenticated:
               membership = _active_membership(request)
               if membership is None:
                   return HttpResponse("No tenant available.", status=403)
               request.tenant = membership.tenant
           return self._get_response(request)

An anonymous request keeps ``request.tenant`` at ``None`` and reaches the login page, and a signed-in user with no membership is refused with ``403``.
The endpoint that switches tenants writes ``request.session["tenant_id"]`` only after it finds a membership row for that pair, so the session never holds a tenant the user cannot reach.

Resolve the tenant from the request host
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A tenant per subdomain serves anonymous visitors, which the membership shape cannot do.
:meth:`~django.http.HttpRequest.get_host` checks the host against ``ALLOWED_HOSTS`` first and raises the ``DisallowedHost`` subclass of :exc:`~django.core.exceptions.SuspiciousOperation` for anything outside it, which Django answers with ``400``.
The middleware therefore reads a name the project published rather than an arbitrary string.

.. code-block:: python
   :caption: notes/middleware.py

   from django.http import HttpResponse
   from notes.models import Tenant

   class TenantMiddleware:
       def __init__(self, get_response):
           self._get_response = get_response

       def __call__(self, request):
           subdomain = request.get_host().partition(":")[0].split(".")[0]
           try:
               tenant = Tenant.objects.get(subdomain=subdomain)
           except Tenant.DoesNotExist:
               return HttpResponse("Unknown tenant.", status=404)
           request.tenant = tenant
           return self._get_response(request)

.. code-block:: python
   :caption: config/settings.py

   ALLOWED_HOSTS = [".example.com"]

A leading-dot entry admits every subdomain of that domain, so the ``Tenant`` row decides which of them the project answers and an unmatched subdomain is a ``404``.
Never pair a host-derived tenant with ``ALLOWED_HOSTS = ["*"]``, because the wildcard hands the host back to the client and the tenant becomes forgeable again.
Pair the shape with the membership check as well when the page is behind a login, so a signed-in user who reaches a host they hold no membership for is refused rather than served.

Read the tenant from a header behind a trusted proxy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A gateway that already knows the tenant can pass the slug down in a header, which keeps the mapping in one place and out of the application.
The header identifies a tenant only while every one of these holds.

- The proxy is the only route to the application, and the application binds to an address the public network cannot reach.
- The proxy derives the value from something it owns, a host mapping, a client certificate, or its own session.
- The proxy sets the header unconditionally on every request it forwards, which overwrites any copy the client sent.
- The application refuses a request whose header is absent instead of falling back to a default tenant.

The third rule is the one that carries the isolation.
A proxy that adds the header only when it is missing leaves the client's own value in place.

.. code-block:: nginx
   :caption: nginx.conf

   location / {
       proxy_set_header X-Tenant $tenant;
       proxy_pass http://127.0.0.1:8000;
   }

.. code-block:: python
   :caption: notes/middleware.py

   from django.http import HttpResponse, HttpResponseBadRequest
   from notes.models import Tenant

   HEADER_NAME = "HTTP_X_TENANT"

   class TenantMiddleware:
       def __init__(self, get_response):
           self._get_response = get_response

       def __call__(self, request):
           slug = request.META.get(HEADER_NAME, "").strip()
           if not slug:
               return HttpResponseBadRequest("Tenant header missing.")
           try:
               tenant = Tenant.objects.get(slug=slug)
           except Tenant.DoesNotExist:
               return HttpResponse("Unknown tenant.", status=404)
           request.tenant = tenant
           return self._get_response(request)

A missing header is a ``400`` and an unknown slug is a ``404``, and neither body repeats what the client sent.
Cross-check the membership rows on top of the header whenever the request also carries an authenticated user, so a proxy misconfiguration alone cannot cross tenants.

Register the middleware last
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Register it last in ``MIDDLEWARE`` so it runs after sessions and authentication, which both the membership shape and the cross-check depend on.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
       "django.middleware.csrf.CsrfViewMiddleware",
       "django.contrib.auth.middleware.AuthenticationMiddleware",
       "django.contrib.messages.middleware.MessageMiddleware",
       "django.middleware.clickjacking.XFrameOptionsMiddleware",
       "notes.middleware.TenantMiddleware",
   ]

Read the tenant through one helper
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every consumer reads the tenant through a single accessor instead of touching ``request.tenant`` directly.
The accessor sits in ``notes/middleware.py`` beside the middleware that sets the attribute.
On error pages where the middleware short-circuited, the attribute is absent and the helper returns ``None``.

.. code-block:: python
   :caption: notes/middleware.py, the accessor

   def get_active_tenant(request):
       """Return the tenant attached to `request` by `TenantMiddleware`."""
       return getattr(request, "tenant", None)

Make the tenant the scope
~~~~~~~~~~~~~~~~~~~~~~~~~

Every tenant-owned model answers which of its rows a tenant may see through ``visible_to`` on its default manager.

.. code-block:: python
   :caption: notes/models.py, the tenant-owned rows

   class NoteQuerySet(models.QuerySet):
       def visible_to(self, scope):
           if scope is None:
               return self.none()
           return self.filter(tenant=scope)

   class Note(models.Model):
       tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
       title = models.CharField(max_length=200)

       objects = NoteQuerySet.as_manager()

The named dependency ``scope`` hands ``visible_to`` the tenant of the request.

.. code-block:: python
   :caption: notes/deps.py

   from django.http import HttpRequest
   from notes.middleware import get_active_tenant
   from notes.models import Tenant

   from next.deps import resolver

   @resolver.dependency("scope")
   def scope(request: HttpRequest) -> Tenant | None:
       return get_active_tenant(request)

The ``next`` application imports ``notes/deps.py`` at startup, so the registration needs no ``AppConfig.ready`` code.
The scope resolves once per render, and every callable of that render shares the value.
A request without a tenant gets ``None``, which ``visible_to`` answers with no rows.
Keep real annotations in ``deps.py`` and in every page module, because the resolver reads them at runtime.

Load rows through the scope
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A list asks for the scope by name.

.. code-block:: python
   :caption: notes/pages/notes/page.py

   from notes.models import Note, Tenant

   from next import Depends, context

   @context("notes")
   def notes(scope: Tenant | None = Depends("scope")) -> list[Note]:
       return list(Note.objects.visible_to(scope))

A page that addresses one row takes it as ``DObject``, named after the route segment that carries its primary key.

.. code-block:: python
   :caption: notes/pages/notes/[int:note]/page.py

   from notes.models import Note

   from next import context
   from next.deps import DObject

   @context("note")
   def note(note: DObject[Note]) -> Note:
       return note

``DObject`` reads the row through ``visible_to(scope)``, so the primary key of another tenant's note answers 404 exactly like a key that matches no row.
A query that skips ``visible_to`` reads the whole table, and no amount of care in the middleware repairs that, so every read of tenant-owned rows starts from it.
See :ref:`topics-di-dobject` for rows nested under a parent row.

Create and edit rows for the tenant
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The forms take the tenant from the same dependency through ``get_form_kwargs``, so neither a factory nor a hidden field carries it.

.. code-block:: python
   :caption: notes/forms.py

   from typing import Any

   from notes.models import Note, Tenant

   import next.forms
   from next import Depends
   from next.deps import DObject

   class NoteCreateForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title"]

       @classmethod
       def get_form_kwargs(cls, scope: Tenant | None = Depends("scope")) -> dict[str, Any]:
           return {"instance": Note(tenant=scope)}

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title"]

       @classmethod
       def get_form_kwargs(cls, note: DObject[Note]) -> dict[str, Any]:
           return {"instance": note}

The create form saves the new row under the tenant of the request, and the edit form loads the row through ``visible_to`` on the render and again on the POST.
The edit form serves pages whose route captures ``note``, such as ``notes/[int:note]/edit/``, see :doc:`use-modelform-for-crud`.

Lift the tenant to every descendant page
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``@context(..., inherit_context=True)`` callable on the workspace root publishes the tenant once, and every nested page reads it by name.

.. code-block:: python
   :caption: notes/pages/page.py

   from notes.models import Tenant

   from next import Depends, context

   @context("tenant", inherit_context=True)
   def tenant(scope: Tenant | None = Depends("scope")) -> Tenant | None:
       return scope

Theme the chrome with a context processor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A context processor turns the tenant's color into a CSS variable for every template.
List it in the page backend ``OPTIONS`` so the file router runs it.

.. code-block:: python
   :caption: notes/context_processors.py

   from notes.middleware import get_active_tenant

   def tenant_theme(request):
       """Surface per-tenant CSS variables to every page template."""
       tenant = get_active_tenant(request)
       if tenant is None:
           return {"tenant_theme": {}, "tenant_theme_css": ""}
       css_vars = {"--tenant-accent": tenant.primary_color}
       css = ";".join(f"{name}:{value}" for name, value in css_vars.items())
       return {"tenant_theme": css_vars, "tenant_theme_css": css}

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
               "OPTIONS": {
                   "context_processors": [
                       "django.template.context_processors.request",
                       "notes.context_processors.tenant_theme",
                   ],
               },
           },
       ],
   }

Prefix asset URLs per tenant
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scope the static backend the same way.
Subclass ``StaticFilesBackend`` and override ``asset_url``.
Read the tenant from the ``request`` keyword argument that the static manager passes to that hook, then prepend the tenant slug to each URL.
The one override reaches the co-located assets and the ``next.min.js`` runtime alike.
Register the subclass in ``STATIC_BACKENDS``.

See :doc:`write-a-static-backend` under *Tenant URL prefix* for the full implementation.

Verification
------------

Send the same path under two tenant hosts and confirm the responses diverge.

.. code-block:: bash
   :caption: confirm tenant isolation

   curl -H 'Host: acme.example.com' http://127.0.0.1:8000/notes/
   curl -H 'Host: globex.example.com' http://127.0.0.1:8000/notes/

The Acme response lists only Acme notes.
The Globex response lists only Globex notes.
A host that matches no tenant row returns ``404``, and so does the page of an Acme note requested under the Globex host.

Under the membership shape the same check signs in as an Acme member, reads the notes page, then signs in as a Globex member and confirms that neither response carries the other tenant's rows.

One negative check belongs in the suite as well.
Send a request that forges the tenant source and confirm it does not cross tenants.
A signed-in Acme member who adds an ``X-Tenant`` header still sees Acme notes under the membership shape, because the database decides.
Under the proxy-fronted shape, send the forged header through the public entry point rather than straight at the application, so the check exercises the stripping rule the isolation rests on.

See also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for ``DObject`` and the ``scope`` dependency.
   :doc:`/content/topics/static-assets/backends` for the request-aware backend contract.
   :doc:`/content/topics/context` for ``inherit_context`` and context processors.
   :doc:`/content/internals/request-lifecycle` for where middleware sits in the request path.
   :doc:`/content/security/overview` for the wider trust boundary the tenant source sits on.
