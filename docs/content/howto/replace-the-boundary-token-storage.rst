.. _howto-replace-the-boundary-token-storage:

Replace the boundary token storage
==================================

Problem
-------

A client-trip boundary carries a token that holds the claims of the reload, the component, its props, the URL, and the permissions.
The default storage keeps those bytes inside the token, so a component with large props overruns ``MAX_BYTES``, and nothing on the server can revoke one token.
The project wants the claims in its own table, a store it already operates.

Solution
--------

Subclass ``TokenStorage``, implement ``put`` and ``get``, and name the class in ``DEFER["TOKENS"]["OPTIONS"]["STORAGE"]``.
The shipped ``StackedTokenBackend`` builds the storage, the seal, and the binding from those options, so replacing one seam leaves the others as they are.
A project that needs a different token scheme altogether replaces the backend instead.

Walkthrough
-----------

Know the layers
~~~~~~~~~~~~~~~

A mint runs the claims through two seams in order.

.. list-table::
   :header-rows: 1
   :widths: 22 40 38

   * - Seam
     - What a subclass implements
     - Shipped
   * - ``TokenStorage``
     - ``put(data, terms) -> bytes`` returns the reference of the claims bytes, and ``get(reference, terms) -> bytes`` returns the bytes of a verified reference, plus the class attribute ``inline`` and the optional ``overflow(data, terms)``.
     - ``InlineStorage``, ``CacheStorage``, ``SpillStorage``
   * - ``TokenSeal``
     - ``seal(payload, salt) -> str`` and ``open(sealed, salt) -> bytes``, plus ``available()`` and the class attribute ``protects``.
     - ``SignedSeal``, ``EncryptedSeal``

The claims are strict JSON, the storage turns those bytes into a short reference, and the seal signs or encrypts the reference into the text after ``version.epoch.profile.`` of the token.
A redeem opens the seal first, so a reference a client forged never reaches the storage, and only then asks the storage for the bytes.
``StorageTerms`` carries the lifetime of the token, ``max_stored_bytes``, and the cache alias, a frozen value the storage only reads.

Write the storage
~~~~~~~~~~~~~~~~~

A table keeps the claims, and the reference is the content address of the bytes, so equal claims share one row.

.. code-block:: python
   :caption: shop/models.py

   from django.db import models

   class StoredClaims(models.Model):
       handle = models.CharField(max_length=64, primary_key=True)
       data = models.BinaryField()
       expires = models.DateTimeField(db_index=True)

.. code-block:: python
   :caption: shop/tokens.py

   import hashlib
   from datetime import timedelta

   from django.db import DatabaseError
   from django.utils import timezone
   from shop.models import StoredClaims

   from next.defer import (
       BoundarySizeError,
       BoundaryStorageError,
       BoundaryTokenError,
       StorageTerms,
       TokenRejection,
       TokenStorage,
   )

   class DatabaseStorage(TokenStorage):
       def put(self, data: bytes, terms: StorageTerms) -> bytes:
           if len(data) > terms.max_stored_bytes:
               msg = f"the claims take {len(data)} bytes, over MAX_STORED_BYTES"
               raise BoundarySizeError(msg)
           handle = hashlib.sha256(data).hexdigest()
           expires = timezone.now() + timedelta(seconds=max(1, terms.lifetime))
           try:
               StoredClaims.objects.update_or_create(
                   handle=handle, defaults={"data": data, "expires": expires}
               )
           except DatabaseError as exc:
               raise BoundaryStorageError(str(exc)) from exc
           return handle.encode()

       def get(self, reference: bytes, terms: StorageTerms) -> bytes:
           try:
               row = StoredClaims.objects.filter(
                   handle=reference.decode(errors="replace"),
                   expires__gt=timezone.now(),
               ).first()
           except DatabaseError as exc:
               raise BoundaryTokenError(TokenRejection.STORAGE_DOWN) from exc
           if row is None:
               raise BoundaryTokenError(TokenRejection.STORAGE_MISS)
           return bytes(row.data)

``put`` raises ``BoundarySizeError`` for claims over its cap, and the page then renders that boundary in the same response instead of sending a token.
``get`` raises ``BoundaryTokenError`` with a ``TokenRejection`` for a reference it cannot answer, and a missing row is ``STORAGE_MISS``, which the browser answers with one reload of the page that mints a fresh token.
The class is built with no arguments once per backend, so it keeps no request state.
A table keeps the claims outside the token, so the class leaves ``inline`` false and a prop is capped by ``MAX_STORED_BYTES``.
A ``put`` whose table is down raises ``BoundaryStorageError``, and the page renders that boundary without a token, while a ``get`` whose table is down raises ``BoundaryTokenError`` with ``STORAGE_DOWN``, which the browser answers with one reload of the page, as ``CacheStorage`` does for its cache.

The seal covers the reference, so under the default signed seal a client cannot name another row.
The expiry is part of the stored claims, and a redeem checks it after the storage answered, so the ``expires`` column only lets a cleanup job delete rows, and a job that deletes the ones in the past is enough.
Claims carry their mint second, so equal claims share a row only within one second.
A storage on a cache or a table that a client can write to, or one that unpickles what it reads, verifies what it reads the way ``CacheStorage`` does with a keyed address, since a poisoned entry would otherwise reach the claims parser.

Configure it
~~~~~~~~~~~~

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFER": {
           "TOKENS": {
               "OPTIONS": {
                   "STORAGE": "shop.tokens.DatabaseStorage",
                   "MAX_STORED_BYTES": 262144,
               },
           },
       },
   }

The options a project leaves out keep their shipped values.
``MAX_STORED_BYTES`` is the largest claims a non-inline storage takes, and a prop value is capped by it instead of ``MAX_BYTES``, which still bounds the token text.
``manage.py check`` reports a path that does not import or is no ``TokenStorage`` subclass as ``next.E219``.
Raising ``EPOCH`` refuses every token minted before it, which is the way to revoke all tokens at once.

Replace another seam
~~~~~~~~~~~~~~~~~~~~

The same options name the others, ``SEAL`` and ``BINDING``, while ``CARRIER`` picks one of the places the client runtime knows, ``"header"``, ``"query"``, or ``"body"``.
A seal declares ``available()`` as a classmethod that answers whether the library it needs imports, and ``manage.py check`` reports a configured one that answers ``False`` as ``next.E230``, since no token can be minted then.
``protects`` is ``False`` for a seal a client can forge, which ``next.E221`` refuses while ``DEBUG`` is off.
The salt a seal receives already carries the header of the token and the digest of the bound subject, and a seal derives its keys from ``SECRET_KEY`` and ``SECRET_KEY_FALLBACKS``, so a key rotation keeps older tokens open.
``BINDING`` takes the dotted path of a ``SubjectBinding`` subclass or of a callable that takes the request and returns the subject as text, see :doc:`bind-tickets-to-your-own-identity`.

Replace the whole backend
~~~~~~~~~~~~~~~~~~~~~~~~~

``DEFER["TOKENS"]["BACKEND"]`` names a ``BoundaryTokenBackend`` for a scheme the stack cannot express.
A subclass implements the three abstract methods a reload calls.

``issue(request, claims, *, tokens=()) -> str``
   Returns the token of a client-trip boundary whose access rule passed.
``open(request, token, *, tokens=()) -> BoundaryClaims``
   Returns the claims a token proves, none of them compared yet, since a reload knows no component before it reads them, or raises ``BoundaryTokenError`` with a reason.
``settle(request, claims, target) -> BoundaryClaims``
   Returns the opened claims once they name the ``BoundaryTarget`` the server resolved the request to and are still alive, or raises ``BoundaryTokenError`` with a reason.

``redeem(request, token, target)`` opens and settles in one call, which a test uses, and ``announce(request, error, component)`` sends ``boundary_token_rejected`` and logs the refusal, so a subclass that raises from ``open`` and ``settle`` fires the signal for every refusal of a reload.
``carrier`` answers where the client puts a token, ``prop_limit(tokens)`` the longest text one prop carries, ``validate(tokens)`` refuses component overrides the backend cannot run under, and ``bound(request, tokens)`` whether a token binds the visitor, which keeps the page out of shared caches and holds unless the subclass answers false.
``profile(token)`` names the overrides a token was minted under, read from its unsealed text, and a reload then opens it under those overrides alone and refuses it for a component that declares others, while ``None``, the default, opens it under the project settings and again under the overrides of the component it resolves to.

Verification
------------

A test mints a token through the configured backend and redeems it, then deletes the row.

.. code-block:: python
   :caption: tests/test_tokens.py

   import pytest
   from django.test import RequestFactory
   from shop.models import StoredClaims

   from next.defer import BoundaryTokenError, TokenRejection
   from next.defer.manager import defer_manager
   from next.defer.tokens import BoundaryClaims, BoundaryTarget
   from next.testing import issue_boundary_token, override_next_settings

   OPTIONS = {"TOKENS": {"OPTIONS": {"STORAGE": "shop.tokens.DatabaseStorage"}}}

   def test_the_claims_live_in_the_table(db) -> None:
       request = RequestFactory().get("/product/7/")
       claims = BoundaryClaims.of(
           "recommendations", "pages/product", url_path="/product/7/"
       )
       target = BoundaryTarget("recommendations", "pages/product", "/product/7/")
       with override_next_settings(DEFER=OPTIONS):
           token = issue_boundary_token(request, claims)
           assert StoredClaims.objects.count() == 1
           backend = defer_manager.tokens()
           assert backend.redeem(request, token, target).component == "recommendations"
           StoredClaims.objects.all().delete()
           with pytest.raises(BoundaryTokenError) as refused:
               backend.redeem(request, token, target)
       assert refused.value.reason is TokenRejection.STORAGE_MISS

``expire_boundary_token`` and ``freeze_token_clock`` test the expiry the same way, see :doc:`test-a-page-with-deferred-boundaries`.
A page of the project that renders a client-trip component and reloads through ``NextClient.get_boundary`` exercises the whole round trip through the storage.

See also
--------

.. seealso::

   :doc:`/content/ref/defer` for the token backend and the ABCs of the stack.
   :doc:`/content/ref/settings` for ``DEFER["TOKENS"]`` and its options.
   :doc:`/content/topics/boundaries/loading` for what a token carries and how a binding scopes it.
   :doc:`/content/security/overview` for what a token reveals to the client.
