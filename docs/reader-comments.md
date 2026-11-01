# Genuine reader comments

Added from production commit `8e1e18e2514bfe9b675aed164fc47aade30c8280`.
No fabricated ratings, downloads, reviews, testimonials or purchaser badges are
created. Existing star ratings and actual download counters remain unchanged.
The text-comment count is separate from the star-rating count.

## Reader flow

Every book page, including book20 and book21, has an Arabic/English comment form:
optional public display name, required email, 5–2,000-character comment and
explicit consent. Email is never put in the URL, local storage, advertising
events, public API response or public listing. Only a purpose-separated HMAC
fingerprint is stored; it is keyed using the existing server-only storage secret.
No email confirmation or marketing message is sent. A typed email is not proof
of identity. Key rotation can change this deduplication fingerprint.

Submissions enter `pending` status. A successful response says the comment has
been received for moderation, not that it has already been published. The same
email may submit one comment per book. Repeated submissions return the same
receipt without replacing the text that has been reviewed. Rate limits and a
honeypot reduce spam; same-origin checks, bounded validation, database constraints
and plain-text DOM rendering protect the new flow.

A **Verified purchase** badge requires the existing HttpOnly, device-bound,
unexpired paid-order session and the same book in that order. The order ID in
local storage is only a hint, never authorization. The submitted email is not
used to claim that a purchase happened. The private order fingerprint is not
public, and one order cannot create several badged comments for the same book.
Reading or commenting consumes no download allowance.

## Moderation

Use the existing Supabase Table Editor for `public.book_comments`, filter
`status = pending`, read the actual submission and set `status = approved`
and `moderated_at` to its actual moderation time to publish it. Set
`status = rejected` for spam, abuse or private information, with an optional
private `moderation_note`. Apply the same rules to positive and negative opinions;
do not select only favorable reviews or invent reader text. There is no public
moderation endpoint or new administrative secret.

The public listing includes only approved comments and only `id`, `display_name`,
`body`, `verified_purchase`, `created_at`. It paginates 20 at a time. The email and
order fingerprints, moderation state/note and pending/rejected text remain private.

## Database boundary

Migration `20261027215243_reader_comments.sql` adds only the new table, its own
indexes and two invoker functions. RLS is enabled; PUBLIC, anon and authenticated
have no table or function privileges. The existing backend's service role alone
submits/lists comments. The familiar RLS-with-no-public-policy advisor notice is
intentional for this server-only table, as with the existing private store tables:
https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy
No existing rows, counters, orders, payment functions, RLS policies or protected
files are edited. There are no sample comments seeded into production.

## Tests and rollout

New offline HTTP tests cover validation, email privacy, spoofed purchase/status
fields, cross-site requests, actual badge authorization, rate limiting and safe
storage errors. Independent Node DOM tests cover plain-text/XSS, moderation,
pagination, language switching without losing form input, double-click prevention,
429 responses, no events and unchanged attribution query strings. Disposable
PostgreSQL CI verifies concurrent deduplication, privileges, constraints, pending
visibility and unchanged existing book statistics. Run the full existing suite
and the added `node tests/test_reader_comments.cjs`.

Apply the additive migration once, deploy the tested commit to the existing
Render branch, wait for `live`, then read public pages/listings only. Do not create
fake production comments for testing. Review environments remain non-indexable
and reject submissions. Rollback the application to the baseline commit to remove
the form; leave the isolated new table in place so genuine submissions are retained.
