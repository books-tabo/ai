# TABO BOOKS: Render preview

This branch hosts the latest storefront and Python checkout server. It is a **preview**: checkout stays disabled until original books, durable storage and payment credentials are configured. The GitHub Pages site on `main` is unaffected.

## Run the preview

Requires Python 3.11+ and `pip install -r requirements.txt`.

```bash
HOST=127.0.0.1 PORT=3000 python3 app.py
```

Open `http://127.0.0.1:3000`. `GET /api/config` reports `checkoutEnabled: false`; `POST /api/orders` returns 503. There are no original PDFs, payment credentials or customer orders in this repository.

## Render preview settings

- Runtime: Python; build: `pip install -r requirements.txt`; start: `python app.py`.
- Branch: `feature/tobo-books-render-preview`; plan: Free for review only.
- Environment: `HOST=0.0.0.0`, `TABO_CHECKOUT_ENABLED=0`, `TABO_PERSISTENT_STORAGE_READY=0`.
- The `/health` endpoint returns 200 for the preview.

## Countries, languages and pricing

- The storefront supports Arabic and English and remembers the visitor's choice in the browser.
- It detects an Arab country from the request locale/browser region or time zone, with a manual selector for all 22 Arab League countries.
- Egypt keeps the base EGP price ladder. Every other supported Arab country uses one fixed USD ladder: $5.99 for one book, then $9.99, $11.99, $13.99, $16.99, $19.99, $21.99, $24.99 and $27.99 for larger bundles.
- Prices no longer depend on an exchange-rate feed: Egypt is always EGP and all other supported countries are always USD.
- Checkout remains unavailable for a currency until its Paymob payment method ID is added to `PAYMOB_PAYMENT_METHODS_JSON`.
- Each book has a dedicated `/books/book1` through `/books/book6` page. Legal and support content uses standalone `/about`, `/contact`, `/privacy`, `/refund` and `/delivery` routes.
- `public.book_stats` stores real file sizes and starts review/download counts at zero. A successful authorized PDF download increments its book counter and records the page count without exposing the private original.

## Before accepting orders

The six original PDFs in the separately held `TABO_BOOKS_BACKEND_READY.zip` must remain outside GitHub. Upload `book1.pdf` through `book6.pdf` to a **private** Supabase Storage bucket named `tabo-originals` using the Storage API or dashboard. The private database table `public.orders` in project `kiglrbfgvjpauugkjuoi` stores orders; its RLS is enabled, with no client policies and no anon/authenticated table grants. Set `SUPABASE_URL` and the `SUPABASE_SECRET_KEY` only on the Render backend. Test database and each private original before setting `TABO_ORIGINALS_VERIFIED=1` and `TABO_PERSISTENT_STORAGE_READY=1`.

Set Paymob **test** secret/public/HMAC keys and `TABO_PUBLIC_URL` in private server configuration. If manual payment confirmation is needed for tests, configure a separate strong `TABO_ADMIN_KEY`. EGP defaults to method ID 5932821; configure the reviewed USD method in `PAYMOB_USD_METHOD_ID`. The signed webhook is `/api/paymob/webhook`. Confirm both EGP and USD with real sandbox transactions and download each purchased watermarked PDF. Only then set `TABO_CHECKOUT_ENABLED=1`. Production Paymob credentials and end-to-end testing need a separate change.

Never put original books, payment secrets or buyer details in a public repository, a web page or a commit.

## Security controls

- The server adds a nonce-based Content Security Policy, HSTS, clickjacking, MIME-sniffing, referrer and browser-permission protections to every response.
- Browser write endpoints accept JSON only, reject cross-site submissions and use per-route rate limits. Render or a CDN/WAF remains responsible for network-level DDoS protection and globally shared limits if the service is scaled beyond one instance.
- Download/status tokens are random 256-bit values. Paid PDFs are read from the private `tabo-originals` bucket, size/type checked, watermarked per buyer and returned with private no-store/no-index headers.
- `public.orders`, `public.book_stats` and `public.book_ratings` use RLS and have no `anon` or `authenticated` grants. Public-schema default privileges are locked down; grant access explicitly for every new table, sequence or function.
- Keep `SUPABASE_SECRET_KEY`, Paymob secrets and `TABO_ADMIN_KEY` only in Render. Rotate a credential immediately if it is pasted into chat, a ticket, a log or any other non-secret channel, then update Render and redeploy.
- Keep pinned dependencies current and rerun the syntax, API-abuse and PDF-watermark tests before each production release.
