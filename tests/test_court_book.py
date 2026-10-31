"""Court book: independent prices and payment-gated private asset access."""
from decimal import Decimal
from unittest.mock import patch

import app
import catalog
import localization
import seo
import store_catalog
from support import HTTPCase
import test_download_http


class CourtBookTests(HTTPCase):
    def test_new_book_keeps_its_full_price_beside_every_existing_pack(self):
        for country, price in [('EG', Decimal('500')), ('KW', Decimal('15.99'))]:
            config = localization.pricing_for_country(catalog.PRICES, country)
            cases = [[], ['book20'], ['book21'], ['book20', 'book21']]
            cases += [offer['books'] for offer in catalog.OFFERS.values()]
            cases += [list(catalog.BOOKS)[:n] for n in range(1, 22)]
            for ids in cases:
                expected = Decimal(str(catalog.total_for_items(ids, config))) + price
                self.assertEqual(Decimal(str(catalog.total_for_items(ids+['book22'], config))), expected)
            self.assertEqual(config['bookPolicies']['book22'], {
                'price': float(price), 'bundleEligible': False, 'previewEnabled': False})
        self.assertEqual(seo.BOOKS['book22']['pageCount'], 419)
        self.assertEqual(seo.BOOKS['book22']['size'], 12185488)
        self.assertEqual(seo.categories_for('book22')[0][0], 'marriage-family')
        self.assertEqual(catalog.FIXED_PRICES['book20'], {'EGP': 500, 'USD': 15.90})
        self.assertEqual(catalog.FIXED_PRICES['book21'], {'EGP': 500, 'USD': 15.99})
        with self.assertRaises(ValueError):
            store_catalog.validate({'independentPrices': {'book22': {'EGP': 1, 'USD': 1}}})

    def test_pending_and_unpurchased_book_are_denied_before_original_read(self):
        # Reuse the existing device-bound paid-session fixture without inheriting its tests.
        order = test_download_http.DownloadHTTPTests.paid(self, 1)
        headers = {'Cookie': app.download_cookie_name(order['id'])+'=synthetic-session'}
        with patch.object(app.store, 'original') as original:
            for status in ('PENDING', 'REFUNDED', 'FAILED'):
                order['status'] = status
                self.install(order)
                self.assertEqual(self.post('/api/download/book22', {'order_id': order['id']}, headers)[0], 403)
            order['status'] = 'PAID'
            order['items'] = [{'id': 'book21', 'name': 'Synthetic'}]
            self.install(order)
            self.assertEqual(self.post('/api/download/book22', {'order_id': order['id']}, headers)[0], 403)
            original.assert_not_called()

    def test_paid_book22_download_reads_only_the_private_original(self):
        order = test_download_http.DownloadHTTPTests.paid(self, 1)
        order['items'] = [{'id': 'book22', 'name': catalog.BOOKS['book22'][0]}]
        self.install(order)
        headers = {'Cookie': app.download_cookie_name(order['id'])+'=synthetic-session'}
        with patch.object(app.store, 'original', return_value=b'%PDF-synthetic') as original, \
             patch.object(app, 'watermarked', return_value=(b'%PDF-synthetic', 419)):
            code, _, response_headers = self.post('/api/download/book22', {'order_id': order['id']}, headers)
            self.assertEqual(code, 200)
            self.assertIn('no-store', response_headers['Cache-Control'])
            original.assert_called_once_with('book22.pdf')
