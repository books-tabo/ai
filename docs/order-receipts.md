# Purchase confirmations and easier checkout fields

Checkout accepts Arabic-Indic, Persian and ASCII phone digits, spaces, parentheses,
hyphens and an international 00 prefix. The server repeats normalization and
validation, retains the existing 8–15 digit rule, and returns field-only error codes.
No country code is guessed and the selected country/prices are unchanged. Inline
Arabic/English errors identify and focus the first invalid field. The phone hint
explains its use for purchase identity/support. Gateway failures and quote refresh
messages are shown in-page; both payment actions remain consecutive.

Email is stored only in the existing private buyer JSON for new orders. It is not
added to public order responses, advertising events, PDF stamps or logs. No existing
customer/order is backfilled or emailed. Purchase confirmation is transactional;
this feature adds no marketing subscription or abandoned-cart campaign.

## Stamped PDF delivery update

See `paid-book-emails.md`: new eligible purchases receive a separate confirmation email with a privately stamped PDF for each book. The legacy plain receipt described below remains for purchases without `bookEmailRequested`. No historical purchases are backfilled.

## Sending flow

The additive migration adds one server-only, RLS-enabled outbox and invoker-only
queue functions. A trigger queues exactly one receipt only on a future
PENDING → PAID transition for a live order with server-set receiptRequested=true.
The existing verification/payment/download RPC definitions remain unchanged.

A single bounded daemon in the existing application claims durable two-minute
leases with SKIP LOCKED, rechecks PAID before sending, and uses Resend's HTTPS
API with a stable paid-receipt/<order-id> idempotency key. Temporary failures
retry with backoff; leases recover after restart. Eight attempts/20 hours bound
retrying inside Resend's documented 24-hour deduplication window. Permanent
provider rejection stops retries. Provider acceptance is recorded as sent; it is
not proof of inbox delivery. No open/click tracking feature is added.

The existing Render free service may sleep; queued delivery resumes after the
application wakes. Do not describe this worker as an always-on delivery SLA.
No additional paid worker/service was provisioned.

The Arabic/English message contains the books, order number, actual charge amount,
USD display quote when relevant, and the existing protected tracking/return link.
It explicitly requires the original purchasing browser/device and links to contact
support. It includes no PDF attachment or original-file URL. It cannot transfer
an entitlement, reset the two-download limit or bypass a missing/expired device
session. Existing orders without an email continue to use their original flow.

## Required private production configuration

Sending stays off until all of these are supplied and verified:

- RESEND_API_KEY: a private sending API key, set only in Render Environment.
- TABO_RECEIPT_FROM: a verified sender on tabobooks.com (or its subdomain),
  e.g. orders@tabobooks.com. Complete the provider's required DNS verification;
  setting the address alone does not verify the domain.
- TABO_RECEIPT_SENDER_VERIFIED=1 after that verification.
- TABO_RECEIPTS_SCHEMA_READY=1 after the migration is applied and checked.
- TABO_RECEIPTS_ENABLED=1 as the last activation step.

A Gmail contact address alone is not a sending credential. The support Reply-To
remains taponoor@gmail.com. No password, API key or customer address should be
pasted into a public repository or test fixture. The frontend announces an emailed
confirmation only when the server reports receiptEmailEnabled=true.

Local/CI tests use independent synthetic orders and a stubbed HTTPS transport,
not real email delivery. Before activation, send an owner-designated test receipt
through the verified provider without creating a production order/Purchase event;
check actual inbox delivery. Do not send test receipts to customers or backfill
old purchases. The checked-in examples use non-deliverable example.invalid links.

## Rollback

Set TABO_RECEIPTS_ENABLED=0 and redeploy to pause sending without affecting checkout.
Keep the outbox for audit/retry decisions; do not bulk resend old messages.
The prior UI/application SHA is 5e1303becdd9d43790a152f7f384875a84f31003. Restoring it
requires no removal of the additive table/trigger: old orders lack receiptRequested
and cannot enqueue. Existing payment credentials, prices, tracking and download
limits must be retained.

Primary API references verified 2026-10-29 (Kuwait):
https://resend.com/docs/api-reference/emails/send-email
https://resend.com/docs/dashboard/emails/idempotency-keys
