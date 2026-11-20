# AutoSEO publishing

The receiver is `POST /api/autoseo/webhook`. Production configuration belongs in
the backend environment, never in frontend code or Git:

- `AUTOSEO_WEBHOOK_SECRET`: exactly the shared token configured in AutoSEO.
- `TABO_PUBLIC_URL`: the canonical HTTPS origin, `https://tabobooks.com`.
- `AUTOSEO_REQUIRE_SIGNATURE=0`: Bearer authentication is mandatory; an included
  HMAC-SHA256 signature is always checked against the original request bytes.
  Set `1` to additionally require the signature on every delivery.
- `AUTOSEO_IMAGE_CACHE_DIR`: optional writable local cache directory; default
  `/tmp/tabo-autoseo-images`.

Apply only `20261026224519_autoseo_publishing.sql` before enabling the integration.
It creates the dedicated `tabo_autoseo_posts`, `tabo_autoseo_media` tables and the
`tabo_autoseo_publish` RPC. RLS, grants and the index apply only to these new
objects. No existing order, download counter, catalog data, RLS policy, payment
flag or existing database function is changed. Browser roles have no direct
access; the backend uses its existing private Supabase adapter.

The RPC atomically saves the article and its downloaded images. Article ID is
the upsert key. Older timestamps cannot overwrite newer content; the original
public slug and publication date remain stable. An upstream slug reused by a
different article gets a suffix. Duplicate HTTP responses lost after commit can
be retried safely.

Public content is server rendered at `/blog/<slug>`, linked from `/blog` and the
store footer. `/blog/sitemap.xml` is advertised in `robots.txt`. Arabic and other
RTL article languages render in their correct direction. Pages include canonical,
Open Graph, BlogPosting and supplied FAQ metadata; article text and links are
sanitized. Existing manually curated `/articles` drafts are not published.

Hero and infographic images are fetched over HTTPS, verified as public network
destinations on every redirect, decoded and converted to WebP, then cached as
local files. Their durable copies are stored in the separate media table, so an
ephemeral instance or redeploy restores files without hotlinking the source.
Limits: 1 MiB incoming JSON, 10 MiB per downloaded image, 25 million source
pixels, 1800px maximum output dimension and 1,000,000 bytes per stored WebP.
Other external HTML images, executable HTML, embeds and inline styles are removed.

Successful publish/update returns `200 {"url":"https://tabobooks.com/blog/<slug>"}`.
An authenticated `{"event":"test"}` returns
`200 {"url":"https://tabobooks.com/test"}` without touching storage. Missing or
wrong authentication/signature is `401`; invalid content is `400`; oversized JSON
is `413`. Download, storage and unexpected failures are retryable `500`, with no
false successful acknowledgement. Do not log tokens or complete webhook bodies.

Offline checks:

```sh
python -m unittest discover -s tests -p test_autoseo.py -v
python -m unittest discover -s tests -v
python -m ruff check --select E9,F63,F7,F82 .
```

The existing isolated PostgreSQL suite also checks concurrent duplicate deliveries,
stable URLs, stale updates, slug collisions, transactional media rollback,
server-only permissions and unchanged existing orders. Its loopback/test-database
safety boundaries remain mandatory; never run it against production.

For activation, deploy the tested branch without merging PR #1, configure the
shared secret privately, then test HTTPS authentication and the `test` event.
In AutoSEO enter the live receiver URL and the matching token, click **Send Test**,
then **Connect Webhook**. A transport test does not publish or create an article.
