# Reader comments

Book pages publish new comments immediately after successful validation and
storage. The public list shows the display name, comment, date and verified
purchase badge. The badge requires a server-verified paid session for the
same book. Ratings and download counters are independent.

An email fingerprint prevents repeated submissions for a book. The public
form has a separate, unchecked choice for offers and new-release updates.
Only new comments with that choice save the normalized address and consent
time in the private subscribers table. The address is unverified. There is
no campaign sender in this feature. Email addresses are never sent to the
public API, browser storage or advertising events.

## Owner dashboard

In the comments and subscribers panel, the owner can remove comments from
the public listing and browse the opt-in list. Removing a comment marks it
rejected, keeping duplicate submissions from republishing it. Older pending
comments remain private. Listing and removal require the existing owner
session, and removal also uses the same-origin check for admin requests.

## Rollout

Apply the instant reader comments migration before deploying the app. It
adds an invoker RPC and a private RLS-enabled subscribers table with service
role grants. The old submit RPC is retained for application rollback.
Use synthetic comments in a disposable test database. The public deployment
can be verified with read-only page and API requests.
