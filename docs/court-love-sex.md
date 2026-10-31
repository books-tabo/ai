# The Court of Love & Sex

`/books/book22` is **محكمة الحب والجنس — أسرار الرغبة والمتعة من خلف الأبواب المغلقة** by TABO NOOR. Its primary category is `/categories/marriage-family`.

Use only the exact owner-selected original, without substituting a later editorial version. Retain its integrity fingerprint in the private release record. The public cover is a metadata-free WebP derivative of its embedded front cover. No PDF is committed to Git.

The approved independent price is **500 EGP / 15.99 USD**. `book22` never contributes to ordinary quantity discounts, never receives the Sigma pair discount and has no preview. Existing prices and pack memberships are unchanged. Product/mobile, public policy, quote and server checkout share the catalog policy. The editor locks this independent price using the existing fixed-price rules.

The original must exist as `book22.pdf` in the existing private `tabo-originals` bucket **before production deployment**. Verify it by authenticated retrieval against the private release record. Check that anonymous public/original/download paths fail. Add `book_stats` with `page_count=419` and `file_size_bytes=12185488`, without overwriting existing review/download counters. No schema change is needed.

Release through a reviewed PR targeting `feature/tobo-books-render-preview`, then the existing `tabo-books-preview` Render service. Preserve payment flags, credentials, prices and tracking. Verify the intended commit is live and check the product, category, cover, policy, disabled preview routes and anonymous download denial. Never mark a production order paid or fire a synthetic Purchase for smoke testing. A real paid transaction requires the buyer to complete payment; isolated tests verify provider confirmation and device-bound protected downloads.

Rollback must preserve the original and metadata for already-paid orders. Removing `book22` from the catalog also prevents its existing paid downloads, so assess paid entitlements before reverting a deployed catalog.
