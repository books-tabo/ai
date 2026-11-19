# Letters of Jasmine — Volume One

Publishes the user-supplied Arabic literary letters collection as `book25` at EGP 250 for Egypt and USD 9.99 elsewhere. Promotes the existing upcoming card into an available book under Literature & Letters. Existing books, curated packs and scheduled Sigma promotions retain their prices.

## Source and delivery

- Supplied PDF: 354 pages, 5,848,018 bytes, 42 letters and a closing poem; the cover labels it Volume One.
- SHA-256: `e0f7440b542b88c8fcefbc93f62aaff1e854e6af8b79242b7c64d278c2f442e1`.
- Stored as `book25.pdf` in the existing private `tabo-originals` bucket. PDF bytes are not committed to Git.
- Uploaded size and the single-part multipart ETag match the source (`401bb2a3158c9b772a7a26be37cd4fe1-1`). Storage permissions are unchanged.
- Book statistics start at zero reviews and downloads with the actual page count and byte size.
- Uses the existing payment-gated, buyer-stamped PDF delivery and two-download limit.
- Limited preview: first 16 pages rendered as browser images, without a PDF download URL.

## Validation

- Stamped all 354 pages of the supplied PDF using a synthetic Arabic buyer; output 9,015,914 bytes in 3.68 seconds within existing resource limits.
- Visually inspected the original cover and a stamped Arabic text page.
- Generated the 16-page sample and rendered preview pages 1, 11 and 16 through the isolated worker.
- Catalog, pricing, checkout and secure-delivery regressions include book25 and preserve existing pack totals.
- Full Python/JavaScript regression results and production smoke checks are recorded in the pull request.
