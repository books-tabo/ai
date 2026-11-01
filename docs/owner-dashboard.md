# TABO BOOKS owner dashboard

The private Arabic dashboard is at `/admin`. It has overview, orders, audience/journey and delivery/follow-up views. No demo statistics or public financial bootstrap is embedded. Anonymous visitors see only the login form. Reports and email delivery require the production flags and authenticated owner cookie.

## Access

`TABO_DASHBOARD_ADMIN_EMAIL` is the single allowed owner address, explicitly selected by the owner during setup. A code is sent only to that configured address using the existing verified Resend sender. Wrong/unknown addresses receive an indistinguishable response. Codes last 10 minutes, allow five attempts and can be consumed once. Requests have a durable one-minute cooldown and five-per-hour owner limit plus HTTP admission limits. A successful code exchange creates a Secure, HttpOnly, SameSite=Strict host-only cookie with an eight-hour absolute session, bound to the existing browser fingerprint. Tokens and codes are hashed with purpose-separated HMACs; no plaintext credentials are stored in SQL or browser storage. Logout revokes the server session. Changing the configured owner email invalidates previous-owner sessions.

This is read-only administration. Dashboard cookies cannot authorize the existing manual-payment endpoint, create a download session, reset download limits or access original PDFs. No customer email, phone, buyer object, provider transaction ID, status key or download credential is included in reports. The page has no advertising/analytics scripts and is noindex/no-store/no-referrer; it is excluded from the sitemap. A link is provided directly to the owner rather than added to customer navigation.

## Data definitions

- Paid sales use current server-confirmed `PAID`, `payment_mode=live`, a non-null transaction and paid_at within the chosen period. Refund/review/reversal/void states and test/legacy records are excluded. This is gross confirmed order value, not net profit or a refund ledger.
- EGP and USD remain separate. Kashier USD-display orders charged in EGP are EGP revenue. Bundle revenue is never artificially allocated to individual books; bestsellers count units in confirmed paid orders.
- The order table/status totals use creation time. Sales graphs/revenue use payment confirmation time. UI states this difference. All calendar boundaries use Asia/Kuwait (UTC+3). Periods are bounded to 93 inclusive days, table pages to 20 orders, offset to 10,000. Order-ID search, status and country filters are validated and parameterized.
- Country means the store country recorded with the order/session, not precise geolocation. Existing Egyptian pricing enforcement remains intact.
- A queued/sent email records the durable receipt state/provider acceptance, not confirmed inbox delivery. A paid order without downloads is identified without asserting a delivery failure.

## First-party visits and attribution

Existing GA4/Ads/Meta/Snap tracking is preserved. The lightweight `store-insights.js` wrapper independently counts only page sessions and existing product/cart/checkout calls; original GA4 calls receive their unchanged arguments. It never emits a Purchase event. Purchases come from the paid order database.

A random HttpOnly cookie represents a fixed 30-minute session. Durable session rows retain first source/medium, store country, public landing path, bounded page-view count, last activity and one timestamp per product/cart/checkout stage. No email, phone, customer name, raw IP, full URL/query, click-ID value or referrer path is retained. Source categories are fixed. Google/Snap click flags and explicit paid UTM mediums can classify paid traffic; fbclid alone is Meta/unknown, not proof of a paid ad. Client-reported web analytics can be blocked or spoofed and does not certify people or ad attribution.

An optional bounded background task links a newly created order to an existing valid session. A database/analytics failure cannot block checkout or create a fake attribution. Historical/unlinked orders remain unassigned. The journey counts sessions created within the date range; a confirmed paid session may have paid later, and steps can be skipped. It is not a manufactured sequential conversion ratio. Recent activity is a last-five-minutes activity count, not a guarantee that the browser remains online.

Visit data begins at `TABO_INSIGHTS_SINCE`; historical GA4 visits are not imported or invented. Session rows are retained up to 180 days with bounded cleanup; login history older than two days is cleaned. Only the three new tables are pruned. Internal counts may differ from GA4. Authenticated GA4 report API access is still unavailable; this dashboard does not claim otherwise.

## Schema and rollout

The additive migration creates only `owner_dashboard_logins`, `owner_store_sessions`, `owner_order_sources`, their indexes and nine invoker RPCs. RLS is enabled; PUBLIC/anon/authenticated have no table/function permissions; explicit backend grants are used. Reports revalidate the owner session inside SQL as well. Existing orders, payments, counters, download functions and policies are unchanged.

1. Run Python/Node checks and rebuild all migrations on isolated local PostgreSQL CI. Fixtures are synthetic; tests refuse non-local/non-test databases.
2. Apply only `20261029012313_owner_dashboard.sql` to the confirmed TABO Books project; verify ACL/RLS and pre/post existing-function fingerprints.
3. Merge only the dashboard PR into `feature/tobo-books-render-preview`. Preserve unrelated PR #1.
4. Merge four environment values into the existing Render service (never replace): `TABO_DASHBOARD_ENABLED=1`, `TABO_DASHBOARD_SCHEMA_READY=1`, owner-approved `TABO_DASHBOARD_ADMIN_EMAIL`, actual UTC `TABO_INSIGHTS_SINCE`. Existing Resend/sender/session binding settings are reused. Do not alter GA4, payment flags, prices or secrets.
5. Capture the deploy caused by the env update; wait for that exact tested commit to become live. Check `/admin` and anonymous report/session denial, tracker exclusion and public checkout config. Do not create a production order, fake Purchase or customer PDF download.

Rollback: disable `TABO_DASHBOARD_ENABLED` and redeploy, or deploy the previous application commit if necessary. Keep the additive tables; never erase existing orders/payment/download data. Do not rotate the shared binding key to disable the dashboard.

## Validation limits

Unit UI/HTTP tests mock all sensitive transports; independent PostgreSQL tests prove OTP/limits/session revocation, permissions, separate currency totals, paid/test/refund exclusions, pagination, source binding and idempotent funnel stages. Actual provider inbox delivery and owner login require the owner's real code and are not fabricated by tests. New auth/analytics fields are not added to customer tracking payloads.
