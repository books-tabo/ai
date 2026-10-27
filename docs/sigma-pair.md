# Sigma companion book and couples offer

`/books/book21` adds **السيجما: أنوثة طاغية** by TABO NOOR, an Arabic PDF with 471 pages and 15 chapters verified from the owner-supplied 10,079,348-byte file. Its public cover is extracted from the embedded first-page image without changing the artwork and resized to 720 × 1080. The original PDF is excluded from Git and must be uploaded as `book21.pdf` to the existing **private** `tabo-originals` bucket before production rollout.

The new book costs **500 EGP / 15.99 USD**. Both Sigma books have no preview and never count toward ordinary quantity discounts. The original `/books/book20` retains **500 EGP / 15.90 USD**.

`/categories/marriage-family` contains precisely these two books and is their primary breadcrumb category. Initial HTML includes descriptions, intended adult readers, verified chapter titles, page counts, real related-book links, canonical/OG/Book metadata, and a link to the special pack. Existing book URLs are unchanged.

`/offers/sigma-couple` combines exactly `book20` and `book21` for **20 USD** when both are present. This is an explicit exception for a named fixed-price pack. Removing either restores the remaining book's individual price. The original USD total is 31.89. This pack and the three-book change pack cost 35 USD together.

The owner has not specified an Egyptian-pound pack price. Both Sigma books therefore total 1,000 EGP in the Egyptian storefront. The offer card explains this and hides its discounted-pack cart action in EGP. Runtime configuration includes only offers available in the selected currency. The server recomputes the total from book IDs; client-supplied prices or offer IDs cannot authorize a discount. USD totals continue through the existing frozen EGP quote and Kashier payment path.

Validation covers independent/new/pair/mixed prices, currency changes, removal and duplicates, synthetic orders and frozen EGP charges, stale-quote rejection, 8,192 server/browser comparisons, product/mobile prices, disabled preview routes, public HTML/links/sitemap and existing Meta/Google/Snap regressions. No real purchase or synthetic production Purchase is used.

Deployment gate: verify the private original's size and checksum, then insert `book_stats` with page_count=471 and file_size_bytes=10079348 without overwriting review/download counters. Keep the bucket private and payment settings unchanged. Review deployments contain no original or payment/database credentials and remain noindex.

Rollback: revert the release and redeploy the prior live application. No schema migration is required. Retain the original and metadata so already-paid `book21` orders remain recoverable; reverting the catalog alone temporarily prevents new-book downloads and must be assessed before rolling back after sales.
