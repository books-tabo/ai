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
- Egypt keeps the base EGP price ladder. Other supported countries use twice the Egyptian price, converted from EGP to the local currency and rounded upward to a readable amount.
- Exchange rates refresh every 12 hours from the configured public feed, with conservative fallback rates. Use `TABO_FX_RATES_JSON` to pin reviewed rates when required.
- Checkout remains unavailable for a currency until its Paymob payment method ID is added to `PAYMOB_PAYMENT_METHODS_JSON`.

## Before accepting orders

The six original PDFs in the separately held `TABO_BOOKS_BACKEND_READY.zip` must remain outside GitHub. Upload `book1.pdf` through `book6.pdf` to a **private** Supabase Storage bucket named `tabo-originals` using the Storage API or dashboard. The private database table `public.orders` in project `kiglrbfgvjpauugkjuoi` stores orders; its RLS is enabled, with no client policies and no anon/authenticated table grants. Set `SUPABASE_URL` and the `SUPABASE_SECRET_KEY` only on the Render backend. Test database and each private original before setting `TABO_ORIGINALS_VERIFIED=1` and `TABO_PERSISTENT_STORAGE_READY=1`.

Set Paymob **test** secret/public/HMAC keys and `TABO_PUBLIC_URL` in private server configuration. If manual payment confirmation is needed for tests, configure a separate strong `TABO_ADMIN_KEY`. EGP defaults to method ID 5932821; configure reviewed IDs for additional currencies in `PAYMOB_PAYMENT_METHODS_JSON`. The signed webhook is `/api/paymob/webhook`. Confirm each currency with a real sandbox transaction and download each purchased watermarked PDF. Only then set `TABO_CHECKOUT_ENABLED=1`. Production Paymob credentials and end-to-end testing need a separate change.

Never put original books, payment secrets or buyer details in a public repository, a web page or a commit.
