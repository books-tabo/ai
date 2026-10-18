# TABO BOOKS backend review

PR #1 remains a draft. **Keep `TABO_CHECKOUT_ENABLED=0`**, including after tests. No merge, production deployment, live payment or live database migration is authorized by this task. This branch is connected to an existing Render service: auto-deploy was off at read-only inspection on 2026-10-18. Updating this PR does not deploy it or change production environment variables.

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

Configured currencies are intersected with a separately verified-capability allowlist. USD also requires `TABO_USD_CAPABILITY_VERIFIED=1`. Unconfirmed methods/currencies remain disabled. These settings record operator attestations; their presence is not evidence of merchant activation. This task adds/advertises no new payment method.

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
