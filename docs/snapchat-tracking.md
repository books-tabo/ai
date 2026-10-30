# Snap Pixel

The Kuwait Sigma sales campaign uses the existing account's public Pixel
`ec1f266b-eb95-49e9-b53d-e6d5b38e1888`. `TABO_SNAP_PIXEL_ID` can override it;
an explicitly empty or invalid value disables Snap tracking.

`snap-tracking.js` runs only on tabobooks.com and www.tabobooks.com. Storefront
events reuse the existing PageView, ViewContent, AddToCart and InitiateCheckout
calls. The adapter maps them to PAGE_VIEW, VIEW_CONTENT, ADD_CART and START_CHECKOUT,
and preserves the existing Meta adapter independently. Review mode loads no Snap
integration. Only public store/product/offer pages and the secured receipt receive
the adapter; pending, activation and preview pages do not.

Purchase data is returned only by the existing authenticated paid download
manifest. The server requires a live, paid, recent order after installation,
a provider transaction and valid catalog IDs. Value and currency are the stored
charge (including EGP for a converted USD display price). The transaction and
deduplication IDs are stable opaque hashes. No customer matching fields, raw
order reference, provider reference or download token are passed to Snap.

On receipts, no external Snap script loads before authenticated purchase data is
available and the query/fragment have been removed from the URL. In-memory and
browser-storage deduplication prevent ordinary refresh duplicates. Tracking
errors do not prevent access to purchased books. This is browser Pixel delivery,
not Conversions API or a durable acknowledgement; blocked requests or closed
tabs can lose events. Automatic Matching was disabled in the account setup.

Validation: `python -m unittest discover -s tests -p test_snap_tracking.py -v`
and `node tests/test_snap_tracking.cjs`. Tests stub delivery and use synthetic
orders; they send no conversion to the live Pixel. Verify real event reception
and purchase optimization availability in Snap Events Manager before starting
campaign delivery. No artificial purchase should be sent to unlock a goal.
