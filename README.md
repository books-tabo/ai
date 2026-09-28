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

## Before accepting orders

The six original PDFs in the separately held `TABO_BOOKS_BACKEND_READY.zip` must remain outside GitHub. Put them in `TABO_DATA_DIR/books/` on private durable storage. The current `orders.json` persistence also lives in `TABO_DATA_DIR`; Render's Free filesystem is ephemeral and cannot retain it across deploys. Provision persistent storage or migrate orders to a managed database and test recovery first.

Set `TABO_ADMIN_KEY`, Paymob **test** keys and `TABO_PUBLIC_URL` in private server configuration. The test-only Paymob flow uses integration 5932821 and signed webhook `/api/paymob/webhook`. Confirm payment via a real sandbox transaction and download each purchased watermarked PDF. Only then set `TABO_PERSISTENT_STORAGE_READY=1` and `TABO_CHECKOUT_ENABLED=1`. Those switches also require every original PDF and a configured payment path before checkout opens. Production Paymob credentials and end-to-end testing need a separate change.

Never put original books, payment secrets or buyer details in a public repository, a web page or a commit.
