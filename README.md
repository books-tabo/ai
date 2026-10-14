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
- Egypt uses the fixed EGP bundle ladder from 199 EGP for one book to 1,899 EGP for 18 books. Every other supported Arab country uses the fixed USD ladder from $9.99 for one book to $99.99 for 18 books.
- Prices no longer depend on an exchange-rate feed: Egypt is always EGP and all other supported countries are always USD.
- Checkout remains unavailable for a currency until the selected payment provider is configured for it.
- Each book has a dedicated `/books/book1` through `/books/book18` page. Legal and support content uses standalone `/about`, `/contact`, `/privacy`, `/refund` and `/delivery` routes.
- `public.book_stats` stores real file sizes and starts review/download counts at zero. `public.order_downloads` enforces an atomic maximum of two downloads per purchased book and increments the public counter only after a protected attempt is claimed.

## Before accepting orders

Original PDFs must remain outside GitHub. Upload `book1.pdf` through `book18.pdf` to a **private** Supabase Storage bucket named `tabo-originals` using the Storage API or dashboard. The private database table `public.orders` in project `kiglrbfgvjpauugkjuoi` stores orders; its RLS is enabled, with no client policies and no anon/authenticated table grants. Set `SUPABASE_URL` and the `SUPABASE_SECRET_KEY` only on the Render backend. Test database and each private original before setting `TABO_ORIGINALS_VERIFIED=1` and `TABO_PERSISTENT_STORAGE_READY=1`.

Choose `TABO_PAYMENT_PROVIDER=kashier` or `paymob` and set `TABO_PUBLIC_URL` in private server configuration. For Kashier, keep the live Merchant ID, Payment API Key and Secret Key in Render only; `/api/kashier/webhook` verifies every event with the Payment API Key before matching the order amount and currency. Hosted checkout enables the documented card and wallet methods. InstaPay uses Kashier's separate Direct API QR/R2P flow and must not be advertised until that method is confirmed on the live merchant account and tested end to end. `KASHIER_CURRENCIES` defaults to `EGP`; add `USD` only after it is enabled for the account. Paymob remains available as a fallback provider through its existing signed `/api/paymob/webhook` flow. Only set `TABO_CHECKOUT_ENABLED=1` after a real low-value payment, webhook, and protected download test succeeds.

Never put original books, payment secrets or buyer details in a public repository, a web page or a commit.

## Security controls

- The server adds a nonce-based Content Security Policy, HSTS, clickjacking, MIME-sniffing, referrer and browser-permission protections to every response.
- Browser write endpoints accept JSON only, reject cross-site submissions and use per-route rate limits. Render or a CDN/WAF remains responsible for network-level DDoS protection and globally shared limits if the service is scaled beyond one instance.
- New purchases never expose a reusable download bearer token in the URL. The checkout browser receives an HttpOnly, Secure, SameSite cookie; payment confirmation exchanges it once for a device-bound download session and the visible success URL contains only a non-secret order ID. Sharing that URL does not share access.
- Each purchased book is downloadable at most twice. The limit is enforced atomically in PostgreSQL, so concurrent requests cannot race past it. Downloading uses same-origin POST requests; old direct GET download URLs are rejected.
- Legacy token links require the exact existing buyer name and phone, are exchanged once, and then become invalid. Legacy paid rows without verifiable buyer data are revoked rather than silently transferred to whoever has the link.
- Paid PDFs are read from the private `tabo-originals` bucket, size/type checked, watermarked per buyer and returned with private no-store/no-index headers.
- `public.orders`, `public.order_downloads`, `public.book_stats` and `public.book_ratings` use RLS and have no `anon` or `authenticated` grants. The download-claim RPC is executable only by `service_role`. Public-schema default privileges are locked down; grant access explicitly for every new table, sequence or function.
- Keep `SUPABASE_SECRET_KEY`, all Kashier/Paymob credentials and `TABO_ADMIN_KEY` only in Render. Rotate a credential immediately if it is pasted into chat, a ticket, a log or any other non-secret channel, then update Render and redeploy.
- Keep pinned dependencies current and rerun the syntax, API-abuse and PDF-watermark tests before each production release.
