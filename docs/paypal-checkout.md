# Optional PayPal Checkout — prepared, not activated

Status: integration prepared but not activated. The live store remains on commit
`8a1e37c05c5370bde5399a078eab30399f04b2ab` with Kashier unchanged. No live
capture has been executed and this migration has not been applied to production.

On 28 October 2026 the owner supplied application credentials independently.
A read-only connectivity check authenticated against Sandbox OAuth (HTTP 200),
while Live OAuth rejected the same keys (HTTP 401). Listing that Sandbox app's
webhooks succeeded (HTTP 200) and returned no registrations. This confirms the
keys' test environment, not merchant capability or a completed payment. No order,
webhook, environment variable or production resource was created or changed.
Rotate the secret shared in chat and store its replacement privately; neither
credentials nor OAuth tokens belong in this repository or review comments.

The owner's requested login reached PayPal's WhatsApp verification screen.
Automatic approval review rejected the secure OTP fill because it represented
six separate digit inputs incorrectly. The user-facing browser handoff then
reported “page unavailable”. Do not bypass that rejection, request OTPs/passwords
in chat, or assume a login in the owner's local browser signs in the cloud browser.
Merchant onboarding and real Sandbox validation remain prerequisites.

## Behavior

- Adds a separate hosted **PayPal in USD** button for an existing USD cart.
- Leaves Egypt's EGP pricing, the default Kashier button, signed USD-to-EGP
  Kashier quote, all bundle rules and the two independent Sigma prices intact.
- Does not silently convert Egyptian EGP prices into a different PayPal charge.
  EGP carts keep Kashier; adding PayPal to that currency would require an explicit
  display/charge conversion policy and separate verification.
- Uses PayPal Orders v2, `CAPTURE`, `NO_SHIPPING`, the server-computed exact USD
  amount, a fixed merchant ID and local order references. No buyer details are
  put in create-order metadata. Hosted approval URLs are strictly allowlisted.
- A return URL or `APPROVED` order never activates books. The server authenticates
  the existing checkout cookie and tracking key, checks the exact provider order,
  captures only an approved PayPal order, and independently retrieves both the
  order and capture to verify the actual merchant, amount, currency, reference
  and completed state.
- Stable UUID `PayPal-Request-Id` headers cover create/capture retries. The private
  append-only journal serializes duplicate callbacks and rejects cross-order
  capture reuse. Refund/review states take precedence over late paid callbacks.
- Signed webhooks are verified through PayPal's fixed verification API and then
  independently reconciled with API state. Certificate URLs are never fetched
  by the store. Unknown events do not grant entitlements; provider lag retries.
- No PayPal SDK dependency or change to the locked runtime packages. Bounded
  HTTPS requests reject redirects and sanitize all gateway failures. OAuth tokens
  are cached in memory with bounded expiry and a short single-flight lock; key
  or mode changes invalidate the cache.
- Return pages have no advertising tags, remove provider identifiers from the
  address bar, are not indexable, and use the existing paid-order delivery path.
  Sandbox orders remain excluded from existing Purchase tracking.

## Independent enablement (all default to off)

Keep `TABO_PAYMENT_PROVIDER=kashier`. Do not switch the default provider.

| Variable | Purpose |
| --- | --- |
| `TABO_PAYPAL_ENABLED` | Explicitly enables new PayPal checkouts only when `1` |
| `TABO_PAYPAL_CAPABILITY_VERIFIED` | Set `1` only after actual merchant/Sandbox checks |
| `TABO_PAYPAL_SCHEMA_READY` | Set `1` only after applying the matching migration |
| `PAYPAL_MODE` | `test` for Sandbox or `live` for the real merchant |
| `PAYPAL_SANDBOX_CLIENT_ID` / `PAYPAL_LIVE_CLIENT_ID` | REST application ID |
| `PAYPAL_SANDBOX_CLIENT_SECRET` / `PAYPAL_LIVE_CLIENT_SECRET` | Private server-side API secret |
| `PAYPAL_SANDBOX_MERCHANT_ID` / `PAYPAL_LIVE_MERCHANT_ID` | Confirmed seller merchant account ID |
| `PAYPAL_SANDBOX_WEBHOOK_ID` / `PAYPAL_LIVE_WEBHOOK_ID` | Verified webhook registration for that same app |

Store these directly in the intended Render service's private environment.
Never paste secrets into chat, source, screenshots or public files. Do not expose
credentials through browser configuration. Production cannot run `test` mode;
a Sandbox service must use `TABO_ENV=paypal-sandbox` and a different public host.

## Before activation

1. Complete merchant login/onboarding using an accessible, approved sign-in flow,
   or have the owner create the REST app and configure credentials independently.
   Verify business-account capability and merchant country; do not infer them
   from browser locale or the owner's location.
2. Apply `20261028133509_optional_paypal_events.sql` to a disposable test database.
   Its new table/RPC do not replace or alter the existing Kashier/Paymob journal,
   constraints or payment function. Only new PayPal orders can be transitioned.
3. Use an isolated Sandbox web service/database, test credentials and a Sandbox
   buyer. Register `/api/paypal/webhook` on that REST app for
   `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.REFUNDED`,
   `PAYMENT.CAPTURE.REVERSED`. Verify success, cancellation, pending capture,
   invalid signatures, retries, full/partial refunds and the purchasing-device
   download flow. Never send synthetic Purchase events to production pixels.
4. Only after those checks: configure separate Live credentials, verify the live
   webhook and seller, apply the reviewed additive journal migration, then enable
   the optional flag. Actual financial test transactions require the appropriate
   user authorization; do not charge a real card merely to test.

Disable only `TABO_PAYPAL_ENABLED` to stop new PayPal checkouts; authenticated
capture/reconciliation for existing PayPal orders still uses the configured
credentials. Keep credentials and callback handling until outstanding PayPal
orders are settled. A rollback must not erase new journal records or remove
support for already-paid PayPal orders. No rollback is needed for Kashier.

## Verification

Offline tests cover both actual client scripts and isolated HTTP/API fixtures:
optional configuration, real-site test-mode rejection, exact server USD prices,
merchant/reference/amount binding, false success flags, pending/unapproved orders,
API failures, stable idempotency, cookies, cross-origin requests, spoofed/lagging
webhooks, capture duplicates, URL cleanup and no premature Purchase. Disposable
PostgreSQL integration tests cover races, capture uniqueness, refund-before-paid,
private journal permissions and unchanged existing Kashier orders/statistics.
The 148-test Python suite, frontend checks and 19 disposable PostgreSQL tests
passed in CI run `40727264819`. Sandbox OAuth and webhook-list connectivity were
then checked independently as described above. End-to-end Sandbox approval,
capture, actual webhook delivery, refunds and merchant-ID matching have **not**
yet been validated. Live credentials and live merchant capability remain
unverified. No isolated hosted test database is currently provisioned.

Official sources reviewed:
- https://developer.paypal.com/api/orders/v2/orders-create
- https://developer.paypal.com/api/orders/v2/orders-capture
- https://developer.paypal.com/api/webhooks/v1/verify-webhook-signature-post
- https://developer.paypal.com/api/rest/reference/idempotency/
