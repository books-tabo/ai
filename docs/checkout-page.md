# Dedicated checkout

Replaces the cart overlay with `/checkout`. Storefront cart actions and existing
`/?cart=1` links reach that page; offer links finish adding their server-priced
pack before navigating. Public book URLs are unchanged. UTM parameters, duplicate
query values and fbclid are retained; the shared advertising scripts also run on
checkout. No payment identifiers are appended to advertising URLs.

The page has no close-overlay or continue-shopping action. Card/wallet checkout
is the first, green payment button; USD PayPal is the last payment button. The two buttons are consecutive, with no explanatory copy between them. The
exact EGP Kashier quote and PayPal USD explanation remain readable below both
buttons before confirmation, connected to their buttons with aria-describedby. Egypt's existing pricing and PayPal currency
eligibility are unchanged. Country/language selection remains available.

Pricing, pack eligibility, signed quote checks and order submission are shared
between storefront and checkout in `checkout-core.js`. Contact fields are not
saved to browser storage. Checkout shows no prices/payment controls until the
server configuration loads. Empty or malformed carts cannot submit an order.
New static assets use content-hash cache versions and the existing CSP nonce.
The checkout page is noindex, no-store and excluded from the sitemap.

After order creation, the browser goes directly to the selected provider's
server-returned payment URL. The existing status link/payment URL are retained
for safe recovery. Payment callbacks, capture verification, currency/merchant/
amount binding and all backend order/download limits are unchanged.

On PayPal return, only a server-confirmed completed capture leads to a request
for the existing device-bound download session, followed immediately by the
server-issued download page. A failed activation goes to the existing tracking
flow. Kashier returns through that flow and automatically activates the paid
order. Temporary activation errors retry with bounded backoff rather than
leaving a paid customer stuck. Redirect flags or an unconfirmed/pending payment
never activate a download. No payment button click consumes either download.

Validation includes real shared scripts with synthetic HTTP fixtures for both
direct providers, exact pack totals, EGP eligibility, query preservation,
malformed/empty carts, no premature Purchase, and paid-only session activation
with transient-error recovery. HTTP tests verify initial checkout content,
asset serving, noindex/cache/CSP handling, tracking isolation and unchanged book
canonical URLs. Existing pricing, advertising and protected-delivery checks
remain in CI. No real order, payment or customer-original download is needed.

Rollback can restore the preceding UI commit `d3d7466` without a database change.
Existing provider return endpoints remain compatible with already-created
orders. Do not remove paid PayPal journal records or disable reconciliation.

Compact layout keeps 44px contact inputs and 48px payment actions while reducing
vertical whitespace on mobile. The final row contains locally embedded PayPal,
Visa and Mastercard artwork, with alt text and fixed dimensions; rendering it
adds no third-party request, script, tracking or payment-provider dependency.
Brand artwork sources (retrieved 2026-10-28):
- PayPal: https://www.paypalobjects.com/webstatic/i/logo/rebrand/ppcom.svg
- Visa header logo: https://www.visa.com/vcom-assets/content/dam/visa/header/Vector.png
- Mastercard symbol: https://www.mastercard.com/content/dam/brandcenter/assets/downloads/mc_symbol_SVG.zip

This layout follow-up can be rolled back to 5ec4d663ca4a87b013c437f1d04529f94444c86d
without a database or payment configuration change.
