# TABO BOOKS backend review

These notes describe the original PR #1 safety review. That review used **`TABO_CHECKOUT_ENABLED=0`** and did not authorize production operations. Subsequent store/payment activation and the 2026-10-19 Meta tracking installation were separately requested by the owner. The example and CI configurations still keep checkout off; this tracking change does not modify payment environment variables, prices, previews, or book contents. The connected Render branch has auto-deploy off, so a code update alone does not deploy it.

## Independent catalog pricing

`catalog.py` defines the server catalog and per-book policies. Sigma (`book20`) costs **500 EGP / 15.90 USD** and is excluded from ordinary bundle quantities. A mixed order is the ordinary bundle subtotal plus Sigma's full price; for example, two ordinary books plus Sigma cost 799 EGP or 33.89 USD. Checkout recomputes this total from book IDs before signing/verifying the existing EGP charge quote.

Sigma has no preview: its reader, info and page routes return 404 before any original-file read. Other books retain their preview and bundle eligibility. Public `bookPolicies` keep card, detail, mobile, cart and tracking values consistent with the server policy.

## Run and test

Python 3.12, `pip install -r requirements-dev.txt`, then:

```sh
TABO_CHECKOUT_ENABLED=0 HOST=127.0.0.1 PORT=3000 python app.py
python -m unittest discover -s tests -v
python -m ruff check --select E9,F63,F7,F82 .
```

`/health` is liveness, not payment readiness. With the example configuration `/api/config` reports false and `/api/orders` returns 503. Python does not auto-load `.env.example`; copy settings into private configuration. No credentials, buyers or original book PDFs belong in GitHub or logs.

## Migrations and real PostgreSQL tests

`supabase/migrations` contains a catalog-based baseline and an additive payment-event upgrade: tables, columns, constraints, indexes, RPCs, RLS and grants. Existing tables/orders are preserved; drift must be reviewed before upgrading. **Neither migration was applied to the live project in this task.** The optional bucket insert runs only where Supabase Storage exists, preserves an existing bucket, and fails if it is public. No original files or invented metadata are seeded.

Browser roles have no table access or sensitive RPC EXECUTE. Download usage is read-only for the server outside its RPC. The claim/event RPCs are SECURITY DEFINER with empty `search_path`, qualified table names and explicit server-only EXECUTE. Defaults are restricted for the migration creator (`postgres`), including the global PUBLIC function default. This does **not** change defaults owned by `supabase_admin` or other creators. Audit those separately; grant/revoke each new object explicitly.

CI provisions PostgreSQL 17 and rebuilds a fresh schema from migrations. Integration tests use independent native database connections and real row locks: 20 concurrent claims, at most two successes, matching usage/aggregates, all 18 books, unpaid/unpurchased/invalid/expired sessions, one legacy activation winner, payment idempotency, revocation ordering and privileges. The restart test launches a fresh HTTP application process for each attempt: two watermarked synthetic-PDF downloads succeed, the third is denied, and both database counters remain two. Its transport adapter calls native PostgreSQL rather than Supabase REST; it does not certify hosted REST/Storage connectivity or HTTPS cookies.

```sh
export TABO_TEST_DATABASE_URL=postgresql://postgres:synthetic_test_only@127.0.0.1:5432/tabo_test
export TABO_ALLOW_TEST_DATABASE_RESET=1
python -m unittest discover -s tests/integration -v
```

This suite **resets the isolated test schema** and refuses non-loopback hosts, names outside `tabo_test*`, and missing reset opt-in. Never point it at production. HTTP tests use clearly identified synthetic fixtures and are not proof of SQL atomicity or a gateway payment.

## Payment handling

Acknowledgement follows the durable atomic event RPC. Supabase errors retain sanitized HTTP/SQLSTATE classification; only 409 + SQLSTATE 23505 is a unique conflict. Network/database failures return 5xx, not a duplicate acknowledgement. A response lost after commit is safe to retry. A provider/mode/transaction/kind ledger plus locked order transitions prevents duplicate entitlement and stale payment reactivation after revocation.

Kashier requires all entitlement-relevant HMAC fields. Its outer `event` is **unsigned** and cannot decide entitlement. A server-authenticated reconciliation read must match the exact merchant order, persisted session, Kashier order, transaction, amount, currency, success and operation. Missing/inconsistent provider state or lookup failure returns 503. Reconciliation permission and actual payload compatibility must be certified on the sandbox merchant before launch. Confirmed refund/void/reversal clears future access but retains counts/audit. Partial refunds suspend the whole order in `REVIEW`; current data cannot allocate a refund to particular books. No allocation or automatic reopening is guessed.

`KASHIER_MODE` selects separate TEST/LIVE hosts and `KASHIER_TEST_*` / `KASHIER_LIVE_*` keys. Generic legacy credentials are accepted only in test mode. Opaque keys cannot be classified by their shape; real merchant/mode verification remains required.

Paymob is deliberately **test only** (`PAYMOB_MODE=live` is rejected). Test-prefixed secret/public keys and explicit `PAYMOB_TEST_<CURRENCY>_METHOD_ID` replace the old hardcoded integration. Official HMAC verification is followed by lookup using signed `order.id`, checking provider, stored order, integration, amount and currency. Unsigned `merchant_order_id` cannot redirect an entitlement. Signed `is_voided` on the bound parent transaction revokes access. A signed parent refund flag or successful non-auth child operation suspends the entire order in `REVIEW` and records a `review_notice`. The child-operation name and cumulative refunded amount are unsigned, so they cannot determine refund allocation, final settlement or reopening. Provider/manual reconciliation must resolve this hold; automatic classification of every refund/reversal shape remains **uncertified**, a Paymob release gate.

Actual charge currencies are intersected with a separately verified-capability allowlist. Direct USD charging still requires `TABO_USD_CAPABILITY_VERIFIED=1`; new Kashier orders now convert USD display prices to EGP as described below. EGP merchant capability remains required. These settings record operator attestations; their presence is not evidence of merchant activation. No new payment method is advertised.

## Sessions, download limits and proxy trust

Permanent entitlement is **two claimed attempts per book per order**, atomically enforced by PostgreSQL. Pre-auth IP admission accommodates 20 complete 18-book bundles per minute behind one NAT. Authenticated work is limited by order + session to `book_count * (2 downloads + 1 retry/rejected request)` per minute. Those work limits never increase durable entitlement. The bounded in-process abuse guard needs a distributed/edge equivalent before multi-instance scaling.

Watermark/preparation failures do not consume an attempt. After preparation, the RPC rechecks paid state, ownership, session and remaining count, updates usage/aggregate, then bytes are streamed. **Disconnection after reservation consumes the attempt.** There is no automatic decrement/reset path. Support exceptions require an audited process. Refund and claim serialize on the same order; an already reserved stream cannot be recalled, but future claims are denied.

Legacy activation requires nonempty verified buyer name/phone, constant-time UTF-8 comparison after NFC/whitespace normalization, unexpired token and atomic one-time conditions. Missing buyer or expiry is rejected. Cookies remain HttpOnly, Secure, SameSite=Strict, Path=/ without Domain. Load the HTTPS return document before its same-origin cookie exchange; redirects never confirm payment. Actual gateway/browser return certification remains an external test requirement.

Buyer sessions now use an independent `TABO_SESSION_BINDING_KEY`, not the Supabase API key. Supabase key rotation does not invalidate newly issued sessions. User-Agent or binding-key changes still deny access. Old rows using Supabase-key binding require a reviewed transition/reverification plan. No automatic migration or recovery endpoint was added; the order URL alone must never recover access.

Only TCP peers explicitly in `TABO_TRUSTED_PROXY_CIDRS` can supply XFF. Walk right-to-left, stopping at the first untrusted hop. Duplicate/malformed chains fail closed. Default is trust-none. Cloudflare-looking headers and CF-Ray presence prove nothing. Pricing and limiting share the validated address; Egyptian pricing uses the existing local IP ranges. Render documents Cloudflare + load balancers + XFF, but the tools used did not establish exact socket-peer CIDRs/header sanitization. Verify these on **staging**, restrict direct origin access, and configure explicit CIDRs before deployment. Local spoofing tests are not proof of a production bypass.

## Preview and resource limits

The preview button/reader remain. No public route serves original/sample PDF bytes; old links return 410. Only bounded raster pages are available to short signed book/browser sessions, with no-store and existing origin guards. Expired/mismatched sessions and out-of-range pages are denied. Images/canvas cannot absolutely prevent saving pixels, inspection or screenshots.

Preview and watermark work share a non-queueing slot acquired before reading an original; saturation returns retryable 503. Limits: 50 MiB source, 2,000 pages, 100 MiB output, 1,400 x 2,000 preview pixels and 48 + 24 MiB caches. These fix the known concurrent-copy/input/output exposure, not arbitrary native-parser expansion. Isolated OS-limited workers and load tests on verified private assets remain scale-up requirements. Opaque `sb_secret_*` keys use `apikey` only; legacy JWT server keys also use Bearer. Both authentication-header paths have isolated adapter tests; actual private-asset authentication with retrieved live credentials was not exercised here.

## Separate activation decision

`TABO_PERSISTENT_STORAGE_READY` and `TABO_ORIGINALS_VERIFIED` are manual attestations, **not connectivity tests or proof of successful payment**. Keep checkout off. Before activation: review/apply migrations to a backed-up staging copy; verify test merchant modes, capabilities and reversal coverage; complete a provider sandbox cycle over HTTPS with cookies and the configured Storage key type; establish exact proxy trust, legacy-session transition and worker resource limits. No real-money transaction is authorized by this PR task.

References checked 2026-10-18: [Kashier webhooks](https://developers.kashier.io/docs/webhooks), [payloads](https://developers.kashier.io/docs/webhooks/payloads), [reconciliation](https://developers.kashier.io/docs/accept-payments/order-reconciliation), [Paymob HMAC](https://developers.paymob.com/paymob-docs/developers/webhook-callbacks-and-hmac/hmac/hmac-transaction-callback), [callbacks](https://developers.paymob.com/paymob-docs/developers/transaction-callbacks), [Supabase keys](https://supabase.com/docs/guides/getting-started/api-keys), [Render ingress](https://render.com/articles/how-render-handles-ddos-attacks).

## Meta domain and browser events (2026-10-19)

The owner supplied public Pixel/Dataset ID `1371060818078535` and the domain-verification tag now in the homepage head. Publishing the tag does **not** prove Meta has verified ownership; confirm the domain's status in Business Settings after publication. Supplying an ID also does not prove the ad account has permission to use it.

The production-host-only browser adapter reports PageView, ViewContent, AddToCart and InitiateCheckout. Purchase comes only from `/api/downloads` **after the existing paid-order + device-bound session authentication**. Its value, currency and book IDs are derived from durable order data, not callback/query parameters. It excludes test/legacy transactions, payments before installation, and payments older than 24 hours. No synthetic purchase is sent to the live Pixel. The receipt strips its query/fragment before loading the external Pixel, retaining the order reference in the browser history entry so refresh still works. Tracking failures must not block purchased-book access.

Purchase has a stable opaque event ID and browser deduplication; a repeated manifest request does not consume a download. Automatic configuration is disabled, no buyer fields are passed, and pending/activation/preview documents load no external tracker. Only storefront/product/receipt CSPs allow the specific required Meta endpoints. Local/staging hostnames never load the external Pixel. `TABO_META_PIXEL_ID` overrides the public default; an explicitly blank value disables the integration.

This is **browser Pixel tracking, not Conversions API**. Blockers, closed tabs, unavailable storage or blocked tracking requests can lose events; local storage and an event ID are not a durable delivery acknowledgement. Confirm event reception in Events Manager/Test Events and certify the actual payment return before advertising. There is no new custom-conversion object or automatic campaign activation in this code change.

Run `node tests/test_meta_tracking.cjs` plus the Python suite. JavaScript tests stub external delivery and exercise the actual receipt script; Python HTTP tests use synthetic authenticated paid/unpaid/revoked/test/old orders. They prove local conditions and payload handling, **not Meta reception, domain verification or a live Kashier payment**. Reference: [Meta's maintained Pixel template](https://github.com/facebook/GoogleTagManager-WebTemplate-For-FacebookPixel/blob/main/template.tpl).

## USD storefront price, EGP charge — owner-requested 2026-10-20 change

The 2026-10-22 presentation update emphasizes the USD total in the cart and
includes the actual EGP charge below it. The authenticated order-status response
provides only a minimal display/charge summary derived from the stored order;
the existing tracking page uses it to retain both amounts before opening Kashier.
For new converted orders, documented `metaData.displayNotes` also sends both
amounts to Kashier's hosted checkout. No currency, rate, charge, refund or payment
authorization is changed by this presentation update. Kashier still controls its
native total and pay-button display; replacing those with a USD primary total
requires its separately gated customer-facing currency feature for this merchant.

Book/bundle display prices and Egyptian pricing are unchanged. For a new Kashier
order displayed in USD, the cart requests a same-origin, no-store quote from
`POST /api/checkout-quote`. A small 13px line immediately below the total states
the exact EGP amount that will be charged, in Arabic or English. There is no
claim that the issuer will deduct precisely the displayed dollar amount: the
card's bank controls its own conversion and fees.

The server reads Kashier's public USD→EGP [exchange-rate endpoint](https://developers.kashier.io/docs/accept-payments/currency-conversion).
This is a rate lookup, not merchant USD activation. Reads are limited to 8 KiB,
8 seconds and one refresh at a time (bounded lock wait); the cache lasts 60
seconds. An invalid, future or older-than-24-hour source timestamp fails closed.
An unavailable rate stops only the new converted quote, not Egyptian EGP checkout.

A domain-separated HMAC quote uses the existing independent server binding key,
lasts five minutes and binds the exact book IDs, country, display USD price and
payment mode. It fixes the EGP amount using decimal ROUND_HALF_UP to two digits.
The order endpoint accepts that signed quote rather than browser-supplied totals
or a newly fetched rate. After expiry or a cart change, the customer must review
a refreshed amount before confirming again. No payment session/order is created
by the quote endpoint.

Apply the additive `20261020123636_checkout_pricing_quote.sql` migration before
deploying this version. It adds nullable `orders.pricing_quote` with a constraint
matching the actual charge, without changing orders, functions, RLS or grants.
The server persists the quoted USD amount/rate/source date/times alongside the
existing `total`/`currency`, which now contain the actual EGP charge. Webhooks,
reconciliation, refunds and Purchase/InitiateCheckout amounts continue to use
those durable charge fields. Historical USD orders stay USD and can still be
reconciled; no existing order is converted.

No Render payment flag, key or merchant capability is changed by this release.
The example and CI still keep `TABO_CHECKOUT_ENABLED=0`; the owner's separately
authorized live setting is retained. Converting the currency is not proof that
the earlier hosted `invalid credentials` error is fixed, nor proof of a
successful card charge or receipt of Purchase by Meta.

Run the Python suite, PostgreSQL integration suite and
`node tests/test_checkout_quote.cjs` / `node tests/test_meta_tracking.cjs`.
The quote HTTP tests use synthetic storage/provider responses; the SQL suite
rebuilds from all migrations and checks quote persistence and charge constraints.
No real-money payment or real customer download is part of these tests.

## SEO review (not approved for production)

See [docs/seo-review.md](docs/seo-review.md) for initial-HTML rendering, metadata, the public catalog, draft articles, review isolation, testing and rollback. Use `python tools/serve_review.py` only on an isolated service without customer or payment credentials. The live start command remains `python app.py`; no payment setting changes are part of this review.
