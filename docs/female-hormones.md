# Female Hormones and the four-book relationship pack

`/books/book23` adds **هرمونات أنثى — ما يحدث في جسدها… وما لا يفهمه الآخرون** by TABO NOOR in `/categories/marriage-family`.

The original is the unmodified `Hormones.pdf` supplied inside the owner's `Hormones.zip`: 436 pages, 10,481,983 bytes. Preserve its SHA-256 in the private release record. The supplied original has 19 chapter headings numbered 1–11 and 13–20; chapter 12 is absent. Do not invent it or advertise twenty completed chapters. Author confirmation or a corrected original is required before production publication. The cover is a metadata-free 720 × 1080 WebP derivative of the embedded front-cover image. No paid PDF or archive is committed to Git.

The book costs **500 EGP / 19.99 USD**, has no preview and never counts toward ordinary quantity discounts. The existing `/offers/sigma-trilogy` URL now represents the four-book pack (book20, book21, book22, book23) at **1250 EGP / 39 USD**. Keep its URL for existing links and saved cart intents. All four actual IDs must be present. Removing any required book restores the best remaining price; overlapping packs cannot stack. Existing ordinary prices and the USD20 Sigma pair stay unchanged. Do not repeat currency-specific prices in promotional descriptions or add independent-price badges.

Before publishing to production, upload the exact original as `book23.pdf` into the existing **private** `tabo-originals` bucket, verify its byte size and checksum, and insert `book_stats` page_count=436 and file_size_bytes=10481983 without overwriting any review/download counters. No schema or permission changes are needed. Never put the original into a public bucket or source repository.

Release through a PR targeting `feature/tobo-books-render-preview` and the existing Render service. Preserve payment settings and keys. Do not deploy the catalog before the private original is verified. Validate product/category/cover, selected-currency prices, complete and partial carts, frozen payment quotes, preview denial and anonymous original/download denial. Keep stored paid-order totals and entitlements unchanged.

Existing paid-only email delivery applies automatically: separate stamped PDF per book after confirmed payment, stamped with buyer name, phone and order; email delivery does not consume the two device-bound download claims. Use synthetic fixtures for payment, download and email checks; never fabricate a production paid status or Purchase event.

The original 84-article plan for the original 21 books remains unchanged. Do not silently add this book to that plan.
