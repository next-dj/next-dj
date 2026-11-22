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
The shipped ``StackedTokenBackend`` builds the codec, the storage, the seal, the binding, and the transport from those options, so replacing one seam leaves the others as they are.
A project that needs a different token scheme altogether replaces the backend instead.

Walkthrough
-----------

Know the layers
~~~~~~~~~~~~~~~

A mint runs the claims through four seams in order.

.. list-table::
   :header-rows: 1
   :widths: 22 40 38

   * - Seam
     - What a subclass implements
     - Shipped
   * - ``TokenCodec``
     - ``dumps(claims) -> bytes`` and ``loads(data, version) -> BoundaryClaims``, plus a classmethod ``available()`` for a library it needs.
     - ``JsonCodec``, ``MsgpackCodec``
   * - ``TokenStorage``
     - ``put(data, context) -> bytes`` returns the reference of the claims bytes, and ``get(reference, context) -> bytes`` returns the bytes of a verified reference.
     - ``InlineStorage``, ``CacheStorage``, ``SpillStorage``
   * - ``TokenSeal``
     - ``seal(payload, context) -> str`` and ``open(sealed, context) -> bytes``, plus ``available()``.
     - ``NoSeal``, ``SignedSeal``, ``EncryptedSeal``
   * - ``TokenTransport``
     - ``extract(request) -> str | None`` and the class attributes ``place`` and ``name``.
     - ``HeaderTransport``, ``QueryTransport``, ``BodyTransport``

The codec turns the claims into bytes, the storage turns those bytes into a short reference, and the seal signs or encrypts the reference into the text after ``version.epoch.`` of the token.
A redeem opens the seal first, so a reference a client forged never reaches the storage, and only then asks the storage for the bytes.
The context carries the lifetime of the token, the key ring, ``max_stored_bytes``, the cache alias, and the digest of the bound subject.

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

   from django.utils import timezone
   from shop.models import StoredClaims

   from next.suspense import (
       BoundarySizeError,
       BoundaryTokenError,
       TokenRejection,
       TokenStorage,
   )
   from next.suspense.tokens.context import TokenContext

   class DatabaseStorage(TokenStorage):
       def put(self, data: bytes, context: TokenContext) -> bytes:
           if len(data) > context.max_stored_bytes:
               msg = f"the claims take {len(data)} bytes, over MAX_STORED_BYTES"
               raise BoundarySizeError(msg)
           handle = hashlib.sha256(data).hexdigest()
           expires = timezone.now() + timedelta(seconds=max(1, context.lifetime))
           StoredClaims.objects.update_or_create(
               handle=handle, defaults={"data": data, "expires": expires}
           )
           return handle.encode()

       def get(self, reference: bytes, context: TokenContext) -> bytes:
           row = StoredClaims.objects.filter(
               handle=reference.decode(errors="replace"), expires__gt=timezone.now()
           ).first()
           if row is None:
               raise BoundaryTokenError(TokenRejection.STORAGE_MISS)
           return bytes(row.data)

``put`` raises ``BoundarySizeError`` for claims over its cap, and the page then renders that boundary in the same response instead of sending a token.
``get`` raises ``BoundaryTokenError`` with a ``TokenRejection`` for a reference it cannot answer, and a missing row is ``STORAGE_MISS``, which the browser answers with one reload of the page that mints a fresh token.
The class is built with no arguments once per backend, so it keeps no request state.

The seal covers the reference, so under the default signed seal a client cannot name another row.
The expiry is part of the stored claims, and a redeem checks it after the storage answered, so the ``expires`` column only lets a cleanup job delete rows, and a job that deletes the ones in the past is enough.
Claims that carry a mint second differ between mints unless ``BUCKET`` floors it, so equal claims share a row only within a bucket.
A storage on a cache or a table that a client can write to, or one that unpickles what it reads, verifies what it reads the way ``CacheStorage`` does with a keyed address, since a poisoned entry would otherwise reach the codec.

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

The same options name the others, ``CODEC``, ``SEAL``, ``BINDING``, and ``TRANSPORT``.
A codec or a seal declares ``available()`` as a classmethod that answers whether the library it needs imports, and ``manage.py check`` reports a configured one that answers ``False`` as ``next.E237`` for the codec and ``next.E230`` for the seal, since no token can be minted then.
``encrypts`` on a seal is ``True`` when two mints of the same claims differ, which ``BUCKET`` defeats.
``BINDING`` takes the dotted path of a ``SubjectBinding`` subclass or of a callable that takes the request and returns the subject as text, see :doc:`bind-tickets-to-your-own-identity`.

Replace the whole backend
~~~~~~~~~~~~~~~~~~~~~~~~~

``DEFER["TOKENS"]["BACKEND"]`` names a ``BoundaryTokenBackend`` for a scheme the stack cannot express.
A subclass implements two abstract methods and, to serve a boundary reload, two more.

``issue(request, claims, *, tokens=()) -> str``
   Returns the token of a client-trip boundary whose access rule passed.
``redeem(request, token, target) -> BoundaryClaims``
   Returns the verified claims or raises ``BoundaryTokenError`` with a reason, comparing the claims with the ``BoundaryTarget`` the server resolved the request to.
``open(request, token, *, tokens=()) -> BoundaryClaims``
   Returns the claims a token proves, none of them compared yet, since a reload knows no component before it reads them.
``settle(request, claims, target) -> BoundaryClaims``
   Returns the opened claims once they name the target and are still alive.

``transport`` answers where the client puts a token, ``prop_limit(tokens)`` the longest text one prop carries, ``validate(tokens)`` refuses component overrides the backend cannot run under, and ``announce(request, error, component)`` receives a refusal ``open`` raised.
A backend that leaves ``open`` and ``settle`` unimplemented serves no boundary reload.
Every redeem refusal also fires ``boundary_token_rejected`` with its reason.

Verification
------------

A test mints a token through the configured backend and redeems it, then deletes the row.

.. code-block:: python
   :caption: tests/test_tokens.py

   import pytest
   from django.test import RequestFactory
   from shop.models import StoredClaims

   from next.suspense import BoundaryTokenError, TokenRejection
   from next.suspense.manager import suspense_manager
   from next.suspense.tokens import BoundaryClaims, BoundaryTarget
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
           backend = suspense_manager.tokens()
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
