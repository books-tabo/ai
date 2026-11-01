# Curated book packs — 2026-10-23

The named packs apply only when every listed book is present in the cart. Existing per-book IDs, stored cart items and checkout/order/download payloads remain book-based.

| Public route | Books | EGP | USD |
|---|---|---:|---:|
| /offers/change-starter | book18, book14, book6 | 300 | 15 |
| /offers/money-and-wealth | book4, book17, book9, book8 | 350 | 20 |
| /offers/self-growth | book18, book14, book6, book2, book19 | 400 | 23 |
| /offers/sigma-couple | book20, book21 | No EGP discount | 20 |
| /offers/sigma-trilogy | book20, book21, book22 | 1000 | 39.99 |

`content/offers.json` is the editorial/price source. `catalog.py` validates membership and computes the authoritative minimum of the ordinary quantity ladder and every eligible disjoint pack combination plus the ordinary remainder. Shared titles are never charged twice. Both Sigma books are excluded from quantity discounts and receive their explicitly configured USD pair or EGP/USD three-book offer; otherwise independent prices apply. See [Sigma pair details](sigma-pair.md). A missing required title removes that pack's eligibility; the cart explains that removal can change the total.

`localization.py` includes only the selected currency's pack prices in public configuration. Browser prices use exact minor units and the same disjoint-selection rules. USD remains the principal displayed price, with the existing signed quote freezing the actual EGP charge through Kashier before confirmation. No checkout flags, merchant credentials, gateway mode, callback behavior, schema or entitlements change.

Offer pages have complete initial HTML, native book links, canonical metadata, matching breadcrumbs and existing language/country controls. Home links to the offer index and presents native pack cards. Clicking a pack from its landing page carries all existing query parameters into the store. After its one-time cart action, only the `offer` dispatch parameter is removed; UTM/fbclid and existing cart contents survive. Repeated clicks do not duplicate titles or AddToCart events. The existing Pixel is enabled on offer landing pages; ViewContent waits for valid pricing and occurs once per page. No Purchase is emitted by a pack action.

The first five-book pack intentionally extends the three-book pack. Choosing both adds only five unique books and charges 400 EGP / 23 USD. Combining the five-book and money packs adds nine unique books and charges 750 EGP / 43 USD. Any unrelated books retain the regular remaining-quantity ladder; all carts receive the least eligible total. Consequently a complete 19-book ordinary catalog now totals 1849 EGP / 102.99 USD; with the original Sigma it totals 2349 EGP / 118.89 USD. Including both Sigma books totals 2849 EGP / 122.99 USD.

Validation includes exact advertised totals, partial/removal/overlap/upgrade cases, client-price tampering, server-issued USD quotes and EGP orders with synthetic gateways, stale quote rejection after cart changes, full public offer HTML and invalid-route 404s. The browser shared pricing is compared against server totals for 8192 selections across both currencies, unrelated books and Sigma. Existing payment/download/security suites remain required in CI. No real purchase, advertising change or synthetic production Purchase is used.

Rollback: revert the curated-offer release commit and redeploy the existing live service. No data migration is required; already-created orders retain their stored totals, items and signed quotes. Prior application is eb4ceff784a983adc745449abe0366dee15a4c31. The separate article drafts remain unpublished.

The three fixed-price books each cost 500 EGP / 19.99 USD. The trio applies automatically to any cart containing all three, including mixed carts, without stacking overlapping packs. On equal totals prefer the pack covering more books so the trio is named in USD even when the retained pair plus court would have the same total. Removing any required title recalculates eligibility. Stored paid orders retain their existing amounts; a stale USD quote cannot authorize a new order at an old display total.
