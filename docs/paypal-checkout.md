# Optional PayPal Checkout — owner-approved live activation

The owner explicitly declined completing the Sandbox transaction on 28 October
2026, requested live activation and will perform the first real payment himself.
No agent-created real payment or capture is authorized by this release.

Live activation requires `TABO_PAYPAL_LIVE_APPROVED=1`, separately from schema
readiness and the checkout enable switch. The Sandbox verification flag remains
`0` on production: owner approval is not recorded as a successful test. Amount,
seller, capture, session and webhook verification remains enforced for every
actual payment. Sandbox still uses its existing verification gate.

At release preparation, production still serves baseline commit
`8a1e37c05c5370bde5399a078eab30399f04b2ab`. The additive migration
`20261028192913_optional_paypal_events.sql` has been applied to TABO Books.
Catalog inspection confirmed the existing payment and download RPC definitions
are unchanged. The new journal has RLS, no public/client read access and no
direct backend insert privilege; only the backend can execute its transition
function. Source deployment and activation are the remaining release steps.

On 28 October 2026 the owner supplied Sandbox and then separate Live application
credentials independently. The first set authenticated only against Sandbox
OAuth; the second authenticated only against Live OAuth (HTTP 200). Listing
webhooks succeeded for both applications. These are connectivity checks, not
proof of merchant capability or a completed payment.

With the owner's explicit authorization, the Live application now has one
webhook registered at `https://tabobooks.com/api/paypal/webhook` for
`PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.REFUNDED` and
`PAYMENT.CAPTURE.REVERSED`. Registration was independently retrieved and verified;
actual event delivery has not been tested. The callback implementation remains
on this review branch and is not yet deployed to the live store.

The four Live configuration values and `PAYPAL_MODE=live` were saved through
Render's merge operation on the existing service. All three PayPal enablement,
capability and schema-readiness flags remain `0`; other payment settings were
not changed. The provided merchant ID has not yet been independently matched
to a completed provider transaction. No credential values or OAuth tokens are
included in the source, fixtures, review comments or local configuration files.

Saving through the Render connector initiated a redeploy of the unchanged
production commit. Deploy `dep-dbh2s5vf3r2c73eoch50` reached `live` at
`2026-10-28T17:18:04.635436Z`. Public checks confirmed that Kashier checkout
remains enabled and no PayPal option is exposed. No PayPal order or capture was
created. Those connectivity checks do not prove a completed payment. The owner
has subsequently elected to complete the real payment check himself after release.

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
| `TABO_PAYPAL_LIVE_APPROVED` | Owner approval to activate Live; does not claim completed Sandbox testing |
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

## Live rollout

1. Use the owner's supplied Live app credentials and merchant ID. OAuth and
   webhook registration were checked; completed payment and merchant capability
   must not be inferred from them or from the browser locale.
2. The journal migration passed disposable PostgreSQL tests and is now applied
   to production. It does not replace existing Kashier/Paymob tables, constraints
   or functions. Its repository version matches the production migration record.
3. Run automated regression checks, merge only this PayPal change into the
   existing service branch, and deploy. Preserve all existing provider settings.
4. Set `PAYPAL_MODE=live`, `TABO_PAYPAL_SCHEMA_READY=1`,
   `TABO_PAYPAL_LIVE_APPROVED=1` and `TABO_PAYPAL_ENABLED=1` on the existing
   service. Keep `TABO_PAYPAL_CAPABILITY_VERIFIED=0`. Verify configuration and
   checkout without creating an order or submitting a real payment.
5. The owner will perform the first real payment and check order activation and
   delivery. Actual Sandbox approval/capture, signed event delivery and refunds
   remain unverified; the separate test service remains available.

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
The 152-test Python suite (Python 3.12 and 3.14.3), frontend checks, 8192 pricing
comparisons, 20 disposable PostgreSQL tests and the Docker startup/isolation
smoke test passed in CI run `40746826120` at commit
`9228a58e4d3bd00b46e0c1a292d2963f369a8361`. Sandbox and Live OAuth/webhook-list
connectivity and the Live webhook registration were checked independently as
described above. End-to-end Sandbox approval, capture, actual webhook delivery,
refunds, merchant-ID matching and live merchant capability have **not** yet been
validated. `testing/paypal-sandbox.Dockerfile` now provides a strictly guarded,
disposable local PostgreSQL test database with synthetic PDFs and the real
controller/payment/download SQL. Its native SQL transport is explicitly distinct
from Supabase's HTTP gateway. This permits isolated provider tests from the same
`books-tabo/ai` repository without creating another Supabase organization/project.
See `testing/README.md`; no production credentials belong in that container.

## Isolated Sandbox deployment — 28 October 2026

The free Render test service `srv-dbh45ks9v7es739a89s0` is running the tested
commit at https://tabo-paypal-sandbox-20261028.onrender.com/ . Deployment
`dep-dbh46denk5ns738tubq0` reached `live`. This is a separate disposable service,
not the production storefront. It contains its own PostgreSQL database and only
one-page synthetic PDF originals. All three PayPal test flags are enabled only
there; the production PayPal flags remain zero.

The Sandbox app's actual seller ID was confirmed by an independently retrieved
unpaid provider order. A Sandbox webhook was registered for completed captures,
refunds and reversals at the test service's `/api/paypal/webhook`; its URL and
subscriptions were independently retrieved and matched.

The browser flow successfully selected Saudi Arabia, applied the existing Sigma
pair offer at USD 20, created a synthetic pending order and opened the genuine
PayPal Sandbox approval page. PayPal then rejected the buyer sign-in credentials
with its incorrect-details message. No approval or capture took place. A valid
Sandbox Personal buyer sign-in is the current blocker to end-to-end validation;
the real merchant login and REST app credentials are different credentials.

Public test pages returned 200 with `noindex, nofollow, noarchive`, a visible
synthetic-test warning and no advertising tag identifiers. An unsigned synthetic
webhook was rejected with 401. The Saudi configuration exposes PayPal in USD and
leaves the unconfigured default Kashier route disabled in this test service.
Requesting Egypt from the non-Egyptian test location retained the existing
server-side geographic pricing restriction; it did not demonstrate an Egyptian
visitor checkout. Preview `robots.txt` intentionally allows crawling so robots
can read the noindex response headers.

Actual approval, capture, signed webhook delivery, refund and browser download
delivery remain unverified. At that Sandbox checkpoint no real payment,
paid-book download, production schema migration or production source deployment
had occurred. The later owner-approved live rollout and additive production
migration are described at the top of this document.

Official sources reviewed:
- https://developer.paypal.com/api/orders/v2/orders-create
- https://developer.paypal.com/api/orders/v2/orders-capture
- https://developer.paypal.com/api/webhooks/v1/verify-webhook-signature-post
- https://developer.paypal.com/api/rest/reference/idempotency/
