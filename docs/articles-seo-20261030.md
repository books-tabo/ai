# Articles and store SEO maintenance — 2026-10-30

## Confirmed public baseline

Production source: `bfb149bb28696110a265439fff27392ee56523fe`.
The live blog contains four Arabic articles. `/robots.txt` advertises both the
store and blog sitemaps; all four article pages return 200 with self-canonical
URLs and BlogPosting metadata. No Search Console reports were accessed, so
this review does not establish actual Google index coverage, rankings or traffic.

The mobile/desktop drawer treated “Articles” as an empty book filter, despite
the live blog. The blog's description was only “مقالات TABO BOOKS”. Article
table-of-contents links pointed at missing IDs: ingestion strips HTML attributes,
including heading targets. The four pages had 18 such links in total.

## Implemented repairs

- When publishing is enabled, the Articles drawer item is a real `/blog` link.
  Blog navigation is also present in book, category, offers and information pages.
- The blog index has a descriptive Arabic heading/description, publication dates,
  CollectionPage/ItemList/BreadcrumbList metadata and crawlable pagination.
  Page 2 has its own canonical; its Previous link leads directly to `/blog`.
- Existing sanitized article HTML receives deterministic, unique heading IDs at
  render time. Same-page TOC links are repaired, including Arabic/percent-encoded
  fragments and diacritics. External citations are left alone. No stored article
  or upstream slug is modified or republished.
- Article pages expose their real supplied publication/update dates, publisher
  link, breadcrumbs, sharing image fallback and preview metadata. No human
  author, expert review or credentials are invented.
- Conservative title/description topic matching adds public book/category links
  and up to three same-language related articles. Unknown topics receive no
  automatic recommendations. Optional related-list/catalog outages do not hide
  the article. No pricing, paid originals or customer records are read for links.
- Blog CSS is versioned; navigation wraps on mobile, and related books use a
  single column on small screens. The site keeps its review/alternate-host noindex.
- The read-only audit now follows both public sitemaps, covers book21, and reports
  duplicate IDs, missing heading/metadata/canonical, noindex and broken fragments.

## Editorial work still needed

The four current articles share money/business themes. Their overlap is a
potential editorial issue, not evidence of measured keyword cannibalization.
Give each future revision a distinct reader question: preparing a budget,
understanding financial behavior, choosing a business book, and evaluating a
legal digital edition.

Some existing copy includes precise file sizes, edition/page counts, book-content
counts and financial-outcome claims without visible primary citations. In
particular, claims about achieving goals “30” faster or retiring ten years early
must be verified against a clearly identified source or removed in an editorial
revision. Do not extend these claims into titles, schema or new articles. New
financial content should explain context and uncertainty and use primary sources.

The three bilingual essays in `content/articles.json` remain drafts. They were
not bulk-published or given invented publication dates as part of technical SEO
maintenance. Publishing additional pages alone is not proof of useful content.

## Validation

- 204 Python tests and all existing Node suites passed locally.
- Five focused regression tests cover existing TOC repair, Arabic fragments,
  unique duplicate headings, unchanged stored articles, visible/schema dates,
  relevant public links, pagination and graceful optional-data failure.
- An offline pass against the four saved public article HTML responses repaired
  all 18 TOC links and produced unique heading targets on every page.
- Production verification must use the deployed pages, both sitemap responses,
  and the repaired drawer link. No orders, payments or downloads are needed.

## References

- https://developers.google.com/search/docs/fundamentals/creating-helpful-content
- https://developers.google.com/search/docs/crawling-indexing/links-crawlable
- https://developers.google.com/search/docs/appearance/structured-data/article
- https://developers.google.com/search/docs/specialty/ecommerce/pagination-and-incremental-page-loading

These changes improve discoverability and page understanding. They do not imply
an indexing deadline, rich-result eligibility or a ranking guarantee.

## Availability follow-up

After the first deployment, the public audit checked 50 paths, including all 21
books and four articles. All final responses passed, but two public HTML requests
initially returned 503 and succeeded on one retry. Catalog refresh contention is
a reproducible cause: readers wait only 200 ms for the shared refresh lock.

Public HTML can now reuse a recently validated catalog snapshot during another
request's refresh. The allowance expires after 30 seconds, keeps the document and
revision paired, and cannot reuse an invalidated or missing cache. Cold starts
without a validated snapshot still fail closed. Checkout, store-config, pricing,
orders and cover authorization retain strict reads. This changes no payment
decision or paid-download entitlement. Two additional tests cover HTTP behavior,
strict-read isolation, age limits and cache invalidation.
