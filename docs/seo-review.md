# TABO BOOKS SEO review — 2026-10-23

Historical review snapshot. See [the 2026-10-30 article/SEO maintenance](articles-seo-20261030.md) for the current live-blog findings and repairs.

This is a review-only change based on live application `1a7e01201f842268cf49faddcc5aff207ad94375` and draft PR #1. The SEO branch is stacked on that branch. Do not merge it or deploy it to the service serving tabobooks.com without owner approval.

## Public rendering

`seo.py` renders the existing index, book and information templates. The same initial HTML is sent to users and crawlers. There is no bot detection, alternate crawler content or client redirect. Text, covers, headings, navigation, metadata and JSON-LD do not depend on pricing or statistics APIs. Existing book URLs and query parameters remain unchanged.

`content/books.json` is the public editorial source used by both SSR and the existing browser rendering. It contains descriptions, audience, topics, actual-format information, FAQ and explicit related-book selections. It contains no original PDF URLs, customer fields or download credentials. The payment catalog remains in `catalog.py`; no pricing ladder or checkout payload was changed. Book statistics load independently, and a single ViewContent is sent after a valid price arrives. Country/language/statistics rerenders do not generate additional ViewContent events. Public SEO script/style references carry a content hash so cached copies cannot mask a reviewed update.

The current repository implements Meta Pixel and a stable authenticated browser Purchase event ID. **It does not contain a Conversions API implementation** (`meta_tracking.py` explicitly documents that limit). Existing Pixel/Purchase code is unchanged. This review does not certify any integration outside this repository or reception inside Meta Events Manager.

## Search identity

- Existing `/books/book1` through `/books/book20` retain their URLs.
- A valid `/book.html?id=bookN` alias keeps its parameters and canonicalizes to the corresponding existing book URL. Invalid IDs return 404 instead of the previous default book.
- Every public canonical is absolute under `https://tabobooks.com`, with tracking/filter parameters excluded only from metadata. The visitor URL is not rewritten.
- Arabic is the indexed edition and initial document language. The existing local English UI translation is preserved. It is not a separate URL or an English book edition; no hreflang or currency-only URLs were added.
- Five populated categories, a category index and `/authors/tabo-noor` bring the production sitemap from 27 to 34 public URLs. Empty novels/articles categories do not gain indexable pages.
- The existing dynamic sitemap is extended, not duplicated. No guessed lastmod dates are generated. robots.txt allows public resources and references the canonical sitemap on production hosts.

## Structured data and eligibility

Initial JSON-LD contains linked Organization, WebSite, WebPage, Person, Book and BreadcrumbList entities, as applicable. No invented ISBN, ratings, downloads, medical qualification, publication day or unsupported Offer is added.

Product/Offer markup is deliberately deferred: prices depend on verified visitor country and may be switched within the same URL. Google's current Product guidance calls for a distinct URL per currency. The owner's instruction preserves the current URLs and prohibits currency-only pages, so emitting a static Offer would be misleading. Book metadata remains useful independently of product rich-result eligibility. Basic Book markup is not a claim of admission to Google's separate book-feed program.

## Content evidence

- Existing catalog descriptions and verified public author biographies were reused, including their existing official biography-source links.
- The current reference **السيجما _ نسخة نهائية (2).pdf** was read: 245 PDF pages, first edition 2026, 15 chapter headings. The heading of chapter 3 is **تحت الاختبار**, as printed in this edition. Its no-preview policy and separate fixed price are retained.
- The current reference **بدون شهادة جامعية _ نسخة نهائية.pdf** was read: 309 PDF pages and 20 chapter headings. An edition number was not inferred from copyright text.
- These page counts include the PDF cover; the UI labels them as PDF page counts.
- All 20 PDF page counts were checked against the live public aggregate `/api/book-stats`, and now appear in initial HTML and Book JSON-LD. The two inspected reference PDFs independently match their public counts. The other 18 counts are sourced from current public store metadata, not an independent reading of each paid original; evidence is in `docs/seo-page-count-sources.json`. Full chapter headings and edition details for those 18 books remain unverified and omitted. No original PDF bytes are committed or served for SEO.

## Editorial drafts

Three original bilingual drafts live in `content/articles.json`, all with `status: draft`:

1. `/articles/learning-with-evidence` — self-directed learning and demonstrable skill, linked to books 19, 18 and 14.
2. `/articles/income-and-commitments` — income, commitments and comparison, linked to books 17, 4 and 9.
3. `/articles/repeatable-first-step` — practical beginnings and habits, linked to books 18, 14 and 6.

The drafts and `/articles` are accessible only in review mode and excluded from the production sitemap. They require separate editorial approval before changing a status to published. No publication date is invented.

## Review isolation

Run `python tools/serve_review.py`. The launcher forces `TABO_SITE_ENV=review`, suppresses the Pixel, refuses payment/database credentials and listens on the provided PORT. The review application rejects all POST actions and private API reads. Preview PDF originals are not connected. It is a visual/read-only review, not a real payment test.

All review responses carry X-Robots-Tag noindex/nofollow/noarchive, and public HTML also has a matching robots meta tag. Alternate hosts also receive noindex. robots.txt permits fetching the noindex directives; it is not used as a confidentiality boundary. The sandbox contains no customer data or paid originals.

## Tests

- Python unit/HTTP suite, including initial HTML for all 20 books, all public sitemap URLs, valid/invalid aliases, author/category links, structured-data content, private-asset denial and review isolation.
- Actual rendered browser script under Node with delayed statistics and configuration: content remains available, country prices update, Sigma remains independent, cart contents survive, UTM/fbclid remain in the landing URL, and ViewContent/AddToCart do not repeat during rerenders.
- Existing quote, bundle, callback, activation, download-limit and Meta Purchase-deduplication suites.
- Local compileall, JavaScript parsing and git diff whitespace checks. CI additionally runs fatal Ruff rules and the existing isolated PostgreSQL integration suite.
- CI passed for application commit `7e0650b6be840975df1f0e84ab2460062051bb88`: 80 Python/HTTP tests, 9 PostgreSQL integration tests, all Node suites, compilation and fatal Ruff rules. [CI run](https://github.com/books-tabo/ai/actions/runs/39901946024). Final page-count metadata validation is also recorded in PR #2 checks.
- The isolated HTTPS review was checked for all 20 book responses: nonempty H1/description/canonical/JSON-LD and both HTTP and HTML noindex. Its sitemap contains the expected 34 canonical production URLs.
- Browser checks covered Arabic/English, Sigma added alone ($15.90) and with book19 ($25.89), three-book business filtering, author links to all 20 books, and an Arabic article draft with its three related books. No payment was submitted.
- Pricing/statistics refreshes preserve existing editorial links and open FAQ state; language changes update the copy once.
- Google Rich Results Test requested sign-in rather than returning a result; Schema.org Validator showed a human-verification challenge. No external rich-result/schema certification is claimed. Search Console's saved Google account was signed out, so no property reports were accessed. No preview indexing request was sent.
- [Google PageSpeed mobile lab test](https://pagespeed.web.dev/analysis/https-tabo-books-seo-review-onrender-com-books-book20/v5jjya532x?form_factor=mobile): performance 100, accessibility 97, best practices 100, SEO 69 because of intentional preview noindex; FCP 1.0s, LCP 1.5s, TBT 0ms, CLS 0. These are one emulated preview run, not real-user or live-store guarantees. Cover CSS now preserves the source aspect ratio with height:auto. Preview noindex is intentionally retained regardless of the SEO score.

## After owner approval

Keep the live service's current payment settings, keys, start command and branch strategy. Review the SEO diff against the current live branch again, run CI, and deploy the approved application without the review launcher or review environment value. No database migration is required. Confirm live pages have no accidental noindex, then submit `https://tabobooks.com/sitemap.xml` in the correct Search Console property and inspect representative book/category URLs.

Search Console access was not available through the connected sources, and its browser flow required signing into a saved but signed-out account. The HTML verification tag was retained; it is not evidence of verified ownership, accepted sitemap status, clean manual-action/security reports or successful indexing. After deployment check Sitemaps, Page indexing, URL Inspection, Manual actions and Security issues in the actual property.

## Rollback

Before promotion record the then-current live application commit. Reverting this SEO change restores previous public rendering and assets; no customer/order rows or schema need reversing. For the present baseline the pre-SEO application is `1a7e01201f842268cf49faddcc5aff207ad94375`. Do not roll back to that historical version if later unrelated approved fixes have landed; revert only the SEO commits. No rollback is needed on production now because it has not received this work.

## Official references

- https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics
- https://developers.google.com/search/docs/appearance/structured-data/product-snippet
- https://developers.google.com/search/docs/appearance/structured-data/book
- https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap
- https://developers.google.com/search/docs/crawling-indexing/block-indexing
- https://developers.google.com/search/docs/crawling-indexing/robots/intro
- https://search.google.com/test/rich-results

Structured data supports understanding and eligibility. Neither it nor a sitemap guarantees an indexing deadline, a rich result or a particular ranking.
