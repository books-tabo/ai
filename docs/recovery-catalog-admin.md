# Catalog administration and opt-in checkout reminders

The existing `/admin` owner session now has **الكتب والعروض** and **استكمال الطلبات**. Production values are never seeded with demo content. Existing payment, entitlement, download and owner-login objects are not altered by the additive migration.

## Catalog editing

The private editor supports bilingual names/descriptions, sanitized uploaded covers, home-page ordering, independent prices for ordinary books, the ordinary quantity-price ladder, and existing ordinary curated packs (title, description, books, prices and availability). Book IDs, PDF locations and preview policy cannot be changed. Sigma independent prices, quantity exclusions, lack of previews and the existing Sigma pack are locked.

An independent ordinary-book price excludes that book from quantity discounts; an explicit ordinary curated pack can still apply. The existing lowest-eligible-total calculation is used on both client and server. Every book remains present exactly once in the home ordering; moving it never renumbers its identity or changes `/books/bookN`.

Drafts are stored separately. Save and publish require a current durable owner session, same-origin requests and the expected draft revision. The editor asks the owner to preview a saved draft before publishing. A concurrent stale save/publish receives 409. The backend validates text, finite two-decimal positive prices, currencies, books, Sigma restrictions and existing sanitized covers before save and before publish. Publication appends an audit version. Existing orders retain their stored titles, amount and currency.

Public HTML, SEO, checkout and pricing read one context-local published snapshot per request. The backend caches it for at most five seconds. Catalog revisions accompany price configuration, quotes and new-order requests; a stale revision receives 409 and requires reviewing refreshed prices before continuing. Configuration retrieval failures block new checkout instead of silently reverting to old prices. Existing paid-download paths do not depend on catalog configuration retrieval.

Image uploads accept at most 1.5 MiB of JPEG/PNG/WebP, use the existing isolated resource-limited image worker and retain only a metadata-free WebP derivative. Draft image endpoints require owner authentication. Public cover URLs serve only images referenced by the published document. No server-side fetching of arbitrary image URLs is supported.

## Reminders

The checkout checkbox is **unchecked** and optional. Consent is recorded only on the order created after the buyer presses a payment button, as `buyer.recoveryConsent=true`. Filling contact fields alone does not enroll a person. There is no backfill for historical orders without explicit consent.

A durable backend queue enrolls only recent pending live orders with that consent, at most one per normalized email per 24 hours. A reminder is due one hour after the original order creation. The worker rechecks live/pending state, global pause and opt-outs immediately before sending, including any confirmed paid order for the same normalized email since enrollment. A payment can race the final network call; a message already accepted by the provider cannot be withdrawn. No client Purchase event is used for eligibility.

Claims are transactionally leased with SKIP LOCKED. Retries use a stable Resend idempotency key, no more than four attempts, and stop within 20 hours of enrollment to remain inside provider deduplication. The worker uses bounded polling and re-enrolls explicitly consented orders if the initial enrollment request failed. Provider acceptance is displayed as sent, not inbox delivery.

Opaque purpose-separated HMAC tokens expire after three days and restore **only book IDs**. The buyer clicks to replace the browser cart, then reviews current prices and creates a normal new payment attempt. These links reveal no contact details, status keys, original payment URL or download authority. A paid original order shows a completed state and the standard order-assistance link. No order is marked paid or reactivated by a reminder.

The unsubscribe URL displays a confirmation page; only POST records the email opt-out and cancels queued reminders. Link-scanner GET requests do not unsubscribe. Customers can stop reminders from the email footer. The owner can pause/resume the worker and view only counts and recent order IDs/states, not customer contacts. All operational tables/functions are backend-only with RLS and explicit grants.

## Rollout and verification

1. Run the Python and Node checks and full isolated PostgreSQL CI. Fixtures refuse nonlocal/non-test DSNs. New tests cover publication conflicts, private ACLs, no-consent/test/paid exclusion, concurrent claims, opt-out/pause, cart-only recovery and dedup expiry.
2. Apply only `20261029170926_recovery_catalog_admin.sql`; compare existing object fingerprints and ACLs before/after. The initial published and draft documents are `{}`, retaining the exact existing catalog and prices.
3. Merge the reviewed feature into the existing production branch. Do not merge unrelated PR #1.
4. Merge `TABO_STORE_ADMIN_ENABLED=1` and `TABO_RECOVERY_ENABLED=1` into the confirmed Render service; preserve every existing variable. Wait for the intended commit to be live.
5. Verify public pricing is unchanged, unchecked consent is visible, the owner editor script is present, anonymous editing/cover access is denied, and recovery pages are noindex/no-store. Do not create fake paid orders, send customer reminders or spend download entitlements as a production smoke test.

Rollback: disable the two new flags and redeploy, retaining additive records. No payment configuration or shared session key needs changing. Published catalog overrides become inactive if catalog administration is disabled, so deliberately review the original catalog prices before using that rollback after the owner has changed prices.
