"""Independent-price catalog and fail-closed preview regressions."""
import unittest
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import Request
from unittest.mock import patch

import app
import catalog
import localization
from support import HTTPCase


class CatalogPolicyTests(unittest.TestCase):
    def test_every_book_has_the_same_price_and_quantity_eligibility(self):
        self.assertEqual(len(catalog.BOOKS), 25)
        self.assertEqual(catalog.FIXED_PRICES, {})
        for country, price in [('EG',149.99),('SA',4.99),('KW',4.99)]:
            config = localization.pricing_for_country(catalog.PRICES,country,at=0)
            self.assertEqual(len(config['prices']),len(catalog.BOOKS))
            self.assertEqual(config['compareAtPrice'],price)
            self.assertNotIn('bookPromotion',config)
            for bid in catalog.BOOKS:
                self.assertEqual(config['bookPolicies'][bid], {'price':price,'bundleEligible':True,'previewEnabled':catalog.preview_enabled(bid)})
                self.assertEqual(catalog.total_for_items([bid],config),price)

    def test_quantity_totals_and_marginal_saving_cover_all_25_books(self):
        for country,unit in [('EG',Decimal('149.99')),('SA',Decimal('4.99'))]:
            config = localization.pricing_for_country(catalog.PRICES,country,at=0)
            self.assertEqual([config['prices'][str(n)] for n in (1,2,3)],
                             [149.99,298.98,447.97] if country=='EG' else [4.99,8.98,12.97])
            for count in range(1,26):
                total=Decimal(str(config['prices'][str(count)]))
                self.assertEqual(total,unit*count-(count-1))
                if count>1:self.assertEqual(total-Decimal(str(config['prices'][str(count-1)])),unit-1)
            # This selection contains no curated pack, including former special titles.
            self.assertEqual(catalog.total_for_items(['book1','book20','book22'],config),447.97 if country=='EG' else 12.97)

    def test_invalid_or_duplicate_books_are_not_priced(self):
        config = localization.pricing_for_country(catalog.PRICES,'EG')
        for ids in [['book20','book20'],['book999'],['book1','book999']]:
            with self.assertRaises(ValueError):catalog.total_for_items(ids,config)


class NoSigmaPreviewHTTPTests(HTTPCase):
    def test_all_sigma_preview_routes_deny_access_before_reading_the_original(self):
        with patch.object(app.store, 'original') as original:
            for book_id in catalog.PREVIEW_DISABLED:
                token = app.book_previews.issue_session(book_id, 'SyntheticBrowser/1')
                headers = {'User-Agent': 'SyntheticBrowser/1', 'X-Tabo-Preview': '1',
                           'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Dest': 'empty',
                           'Cookie': '__Secure-tabo_preview_' + book_id + '=' + token}
                for suffix, expected in [('', 404), ('/info', 404), ('/page/1', 404), ('/page/16', 404), ('.pdf', 410)]:
                    with self.assertRaises(HTTPError) as caught:
                        self.opener.open(Request(self.base + '/previews/' + book_id + suffix, headers=headers), timeout=5)
                    self.assertEqual(caught.exception.code, expected)
                    self.assertNotIn('Set-Cookie', caught.exception.headers)
            original.assert_not_called()
