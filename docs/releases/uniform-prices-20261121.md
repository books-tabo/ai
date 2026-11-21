# Uniform catalog pricing — 21 November 2026

All 25 published books now cost EGP 149.99 in Egypt or USD 4.99 elsewhere. Former standalone-price exceptions are removed. Preview restrictions, private PDF paths, download entitlements and historical order amounts are unchanged.

For n distinct books the quantity total is n × unit price − (n − 1). Two books cost EGP 298.98 / USD 8.98; three cost EGP 447.97 / USD 12.97. Every book after the first saves one unit of the displayed currency from the total. The quantity ladder covers all 25 titles.

## Curated packs

| Pack | Books | EGP | USD |
|---|---:|---:|---:|
| Start Your Change | 3 | 409.99 | 10.99 |
| Money and Wealth | 4 | 539.99 | 13.99 |
| Self-Growth and Progress | 5 | 669.99 | 16.99 |
| Sigma for Couples | 2 | 279.99 | 7.99 |
| Sigma and Marriage Relationships | 4 | 539.99 | 13.99 |
| Understanding Married Life | 3 | 409.99 | 10.99 |

The cart applies the quantity rule to the entire selection, then chooses the largest savings from non-overlapping eligible packs. Each pack saves its standalone quantity total minus its advertised pack price. This preserves the per-additional-book saving across packs and extra books without counting a title twice.

Retires both old Sigma campaigns and their countdowns. Updates pack copy, price fallbacks, comparison prices and the owner's quantity table. Changes the checkout pricing revision so clients using the old prices must refresh before a new order or quote can proceed. No database or Render settings change is required: the live published and draft catalog overrides were both empty at revision 0.

## Validation

266 Python tests pass, including exact country prices, synthetic order amounts, frozen exchange-rate quotes, PayPal amounts, retired campaigns, old-revision rejection, download protection and two-download limits. All 17 Node suites pass, including 65,536 server/browser pack-price comparisons; exhaustive single-book additions also check monotonic totals and the extra-book saving. No production purchases, payment webhooks or private book downloads are used.
