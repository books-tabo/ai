# Purchase journey and email-verified order assistance

## Scope

- `ga4-tracking.js` sends public-page views and allowlisted GA4 ecommerce events for book views, add-to-cart, checkout start and selected payment provider. The authenticated, server-confirmed paid download manifest supplies the purchase amount/currency and opaque stable transaction identifier. No per-book price allocation is invented for bundles.
- `TABO_GA4_ID` must be the owner's real GA4 web-stream Measurement ID. `TABO_GA4_PURCHASE_SINCE` must be the actual UTC activation time. Both start empty; Google Ads' AW identifier is not a GA4 identifier. No historical purchase backfill.
- GA4 and Google Ads share one gtag loader but use explicit destinations. Existing advertising conversions remain independently deduplicated. Tracking blockers or a buyer never returning to the authenticated receipt can prevent browser measurement; this is not a server-side GA4 completeness guarantee.
- Google Analytics access: https://analytics.google.com/analytics/web/ . After selecting the correct property, use Reports → Monetization → Purchase journey (or its equivalent in the property's report collection) and compare Country and source/medium. Actual property/stream and dashboard link are pending authenticated account access; no analytics property was invented or created.
- Review the web stream's enhanced measurement options before activation: keep automatic form interactions and unsolicited download/link collection disabled for this store. Configure cross-domain/payment-provider attribution using the actual approved provider domains if needed; do not change Google Ads conversion goals or count a GA4-imported purchase as another primary conversion alongside the existing AW purchase without a separate decision.

## Help page

`/order-help` is linked from the main/book/info footers, contact/delivery pages, success page and future purchase emails. It requires the order ID plus the purchase email. The API returns the same generic challenge response whether or not a match exists. Codes are delivered only to the persisted buyer address. Codes expire in 10 minutes, permit five verification attempts and can be consumed once. A verified session lasts 15 minutes in a Secure, HttpOnly, SameSite=Strict host-only cookie, bound to the existing browser fingerprint.

The server-only additive migration creates two RLS-protected tables and six invoker RPCs with explicit backend-only grants. Durable limits apply across requests/processes: five matched challenges per email/hour, ten per client fingerprint/hour and a one-minute order cooldown. Receipts require a server-confirmed PAID live order, three requests/day/order maximum and a ten-minute cooldown. Queue leases, retry bounds and provider idempotency handle races. Provider bodies and customer data are not logged.

Verification permits viewing a strict order summary and requesting a transactional receipt again. It does **not** issue a checkout/download session, change two-download limits, reset used downloads, alter the buyer record or override refund/reversal handling. Returned email/order links still pass through the original-browser download checks. If the original browser is unavailable, the customer contacts support rather than receiving a bypass.

The help page is noindex/no-store/no-referrer with no advertising/analytics scripts; it is excluded from the sitemap. No email, OTP or order secret is stored in browser local storage. Resend transport and existing verified sender are reused; requests are queued, not a guarantee of inbox delivery. The free Render instance can delay delivery while asleep; processing resumes when awake.

## Release and rollback

1. Run Python, Node and isolated PostgreSQL integration checks. PostgreSQL tests refuse non-local databases and use synthetic data only.
2. Apply `supabase/migrations/20261029002159_order_help.sql` to the confirmed TABO Books project. Inspect RLS/grants and verify existing payment/download/receipt function fingerprints remain identical.
3. Merge only this feature into `feature/tobo-books-render-preview`. Set `TABO_ORDER_HELP_SCHEMA_READY=1` and `TABO_ORDER_HELP_ENABLED=1` by merging these two environment keys in Render; this triggers deployment. Do not replace other keys or change payment flags.
4. Wait for the exact commit to be live. Read-only public checks: `/health`, `/order-help`, public pages/config; unauthenticated help status must be denied. Never send synthetic production purchases or customer-email tests.
5. GA4 stays disabled until the real owner-authorized web-stream ID and activation time are supplied. Test only with mocked Google transport; no fake production Purchase events.

Rollback: merge only `TABO_ORDER_HELP_ENABLED=0` and/or empty `TABO_GA4_ID` as applicable, then deploy the previously live application commit if necessary. Keep the additive tables for audit; do not drop tables, reset orders/downloads or revert unrelated application changes. Existing receipt email functionality has its own independent flag.

## Validation boundaries

Unit/HTTP tests use a synthetic in-memory store; SQL atomicity/permissions are proven separately by isolated PostgreSQL CI. Browser tests stub all Google and email transport. No real payment, customer order, customer download or inbox-delivery test is performed by this release.
