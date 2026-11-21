"""Court book: independent prices and payment-gated private asset access."""
from unittest.mock import patch

import app
import catalog
import localization
import seo
from support import HTTPCase
import test_download_http


class CourtBookTests(HTTPCase):
    def test_court_and_sigma_share_uniform_prices_without_changing_preview_policy(self):
        for country,price in [('EG',149.99),('SA',4.99)]:
            config=localization.pricing_for_country(catalog.PRICES,country,at=0)
            self.assertEqual(config['bookPolicies']['book22'], {'price':price,'bundleEligible':True,'previewEnabled':False})
            self.assertEqual(catalog.total_for_items(['book22'],config),price)
            self.assertEqual(catalog.total_for_items(['book22','book1'],config),298.98 if country=='EG' else 8.98)
        self.assertEqual(seo.BOOKS['book22']['pageCount'],419)
        self.assertEqual(seo.BOOKS['book22']['size'],12519883)
        self.assertEqual(seo.categories_for('book22')[0][0],'marriage-family')
        self.assertEqual(catalog.PREVIEW_DISABLED, {'book20','book21','book22','book23','book24'})

    def test_pending_and_unpurchased_books_are_denied_before_original_read(self):
        # Reuse the existing device-bound paid-session fixture without inheriting its tests.
        order = test_download_http.DownloadHTTPTests.paid(self, 1)
        headers = {'Cookie': app.download_cookie_name(order['id'])+'=synthetic-session'}
        with patch.object(app.store, 'original') as original:
            for bid in ('book22','book23','book24','book25'):
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
        for bid,pages in [('book22',419),('book23',457),('book24',370),('book25',354)]:
            app.RATE_LIMITER = app.SlidingWindowLimiter()
            order = test_download_http.DownloadHTTPTests.paid(self, 1)
            order['items'] = [{'id': bid, 'name': catalog.BOOKS[bid][0]}]
            self.install(order)
            headers = {'Cookie': app.download_cookie_name(order['id'])+'=synthetic-session'}
            with patch.object(app.store, 'original', return_value=b'%PDF-synthetic') as original, \
                 patch.object(app, 'watermarked', return_value=(b'%PDF-synthetic', pages)):
                code, _, response_headers = self.post('/api/download/'+bid, {'order_id': order['id']}, headers)
                self.assertEqual(code, 200)
                self.assertIn('no-store', response_headers['Cache-Control'])
                original.assert_called_once_with(catalog.BOOKS[bid][1])
