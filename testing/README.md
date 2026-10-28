# Isolated PayPal Sandbox

This is test infrastructure for `books-tabo/ai`, not the live store's start command.
Build `testing/paypal-sandbox.Dockerfile` and run the image as its non-root user.
No new Supabase project, copied customer records or production database secret is
needed. PostgreSQL 17 runs on loopback inside the disposable container; its data
is removed when the container stops. Restarting requires a new test checkout.

The existing store controller, PayPal module, catalog and payment/download SQL
functions run unchanged. Only the private storage transport is replaced with
local SQL. This does not test Supabase's HTTP gateway itself. The normal CI
PostgreSQL job also exercises this transport with completed payment, refund,
session binding and exactly two downloads. The generated PDF is clearly marked
synthetic; original paid PDFs are excluded from the Docker build context.

The process rejects live PayPal, Supabase, Kashier, Paymob, admin and ad credentials,
production hostnames and non-local/non-test database DSNs. Review mode disables
indexing and advertising tags. The visible test notice is added only inside the
container. Visitor name/phone values are replaced with synthetic values before
database insertion. Other payment/admin/comment write routes are disabled.
Only the selected synthetic payment/download APIs bypass the normal read-only
review routing, using thread-local context. HTML remains in review mode and the
download manifest explicitly omits all advertising Purchase payloads.

Start without credentials for the CI health check. For actual provider testing,
configure only these private runtime values on the isolated Render service:

- `PAYPAL_SANDBOX_CLIENT_ID`, `PAYPAL_SANDBOX_CLIENT_SECRET`
- `PAYPAL_SANDBOX_MERCHANT_ID`, matched to the test app's actual seller
- `PAYPAL_SANDBOX_WEBHOOK_ID`, registered to this service's `/api/paypal/webhook`
- `TABO_PUBLIC_URL`, the exact HTTPS URL returned for that service
- `TABO_PAYPAL_ENABLED=1`, `TABO_PAYPAL_CAPABILITY_VERIFIED=1`,
  `TABO_PAYPAL_SCHEMA_READY=1` only for the isolated test service

The Docker image sets the strict environment markers. The bootstrap enables
checkout/storage only after creating the disposable schema. Kashier remains
unconfigured here; the actual PayPal option is independently gated in a USD cart.
Use a Sandbox personal buyer (not the Sandbox seller) and fictional contact data.
Never charge a real card or send synthetic Purchase events to production tags.

All live-service PayPal flags stay off until provider approval/capture, signed
webhook delivery, refunds and synthetic download delivery have been reviewed.
