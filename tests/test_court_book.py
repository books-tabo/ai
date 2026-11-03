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
    def test_court_keeps_full_price_except_explicit_four_book_pack(self):
        for country, price in [('EG', Decimal('500')), ('KW', Decimal('19.99'))]:
            config = localization.pricing_for_country(catalog.PRICES, country)
            cases = [[], ['book20'], ['book21'], ['book20', 'book21'], ['book23'], ['book20','book21','book23']]
            cases += [offer['books'] for offer in catalog.OFFERS.values() if 'book22' not in offer['books']]
            cases += [list(catalog.BOOKS)[:n] for n in range(1, 22)]
            for ids in cases:
                expected = (Decimal(str(catalog.total_for_items([b for b in ids if b not in ('book20','book21','book23')], config))) + Decimal('1250' if country == 'EG' else '49')
                            if {'book20','book21','book23'} <= set(ids) else Decimal(str(catalog.total_for_items(ids, config))) + price)
                self.assertEqual(Decimal(str(catalog.total_for_items(ids+['book22'], config))), expected)
            self.assertEqual(config['bookPolicies']['book22'], {
                'price': float(price), 'bundleEligible': False, 'previewEnabled': False})
        self.assertEqual(seo.BOOKS['book22']['pageCount'], 419)
        self.assertEqual(seo.BOOKS['book22']['size'], 12185488)
        self.assertEqual(seo.categories_for('book22')[0][0], 'marriage-family')
        self.assertEqual(catalog.FIXED_PRICES['book20'], {'EGP': 500, 'USD': 19.99})
        self.assertEqual(catalog.FIXED_PRICES['book21'], {'EGP': 500, 'USD': 19.99})
        with self.assertRaises(ValueError):
            store_catalog.validate({'independentPrices': {'book22': {'EGP': 1, 'USD': 1}}})

    def test_pending_and_unpurchased_books_are_denied_before_original_read(self):
        # Reuse the existing device-bound paid-session fixture without inheriting its tests.
        order = test_download_http.DownloadHTTPTests.paid(self, 1)
        headers = {'Cookie': app.download_cookie_name(order['id'])+'=synthetic-session'}
        with patch.object(app.store, 'original') as original:
            for bid in ('book22','book23'):
                for status in ('PENDING', 'REFUNDED', 'FAILED'):
                    order['status'] = status
                    self.install(order)
                    self.assertEqual(self.post('/api/download/'+bid, {'order_id': order['id']}, headers)[0], 403)
                order['status'] = 'PAID'
                order['items'] = [{'id': 'book21', 'name': 'Synthetic'}]
                self.install(order)
                self.assertEqual(self.post('/api/download/'+bid, {'order_id': order['id']}, headers)[0], 403)
            original.assert_not_called()

    def test_paid_court_and_hormones_read_only_their_private_original(self):
        for bid,pages in [('book22',419),('book23',457)]:
            order = test_download_http.DownloadHTTPTests.paid(self, 1)
            order['items'] = [{'id': bid, 'name': catalog.BOOKS[bid][0]}]
            self.install(order)
            headers = {'Cookie': app.download_cookie_name(order['id'])+'=synthetic-session'}
            with patch.object(app.store, 'original', return_value=b'%PDF-synthetic') as original, \
                 patch.object(app, 'watermarked', return_value=(b'%PDF-synthetic', pages)):
                code, _, response_headers = self.post('/api/download/'+bid, {'order_id': order['id']}, headers)
                self.assertEqual(code, 200)
                self.assertIn('no-store', response_headers['Cache-Control'])
                original.assert_called_once_with(bid+'.pdf')
