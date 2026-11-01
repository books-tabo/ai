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
    def test_fixed_price_is_added_after_every_ordinary_bundle_in_both_currencies(self):
        ordinary = [book_id for book_id in catalog.BOOKS if book_id not in catalog.FIXED_PRICES]
        self.assertEqual(len(ordinary), 19)
        for country, fixed in [('EG', Decimal('500')), ('KW', Decimal('19.99'))]:
            config = localization.pricing_for_country(catalog.PRICES, country)
            for count in range(20):
                ids = ordinary[:count]
                subtotal = Decimal(str(config['prices'][str(count)])) if count else Decimal(0)
                if country=='EG' and count>=17: subtotal=Decimal({17:1749,18:1849,19:1849}[count])
                if country=='KW' and count==19: subtotal=Decimal('102.99')
                for mixed in [ids + ['book20'], ['book20'] + list(reversed(ids))]:
                    self.assertEqual(Decimal(str(catalog.total_for_items(mixed, config))), subtotal + fixed)
                self.assertEqual(Decimal(str(catalog.total_for_items(ids, config))), subtotal)

    def test_known_mixed_totals_and_no_bundle_slot_for_sigma(self):
        for country, expected in [('EG', [500, 699, 799, 2849]), ('KW', [19.99, 29.98, 37.98, 142.98])]:
            config = localization.pricing_for_country(catalog.PRICES, country)
            selections = [['book20'], ['book20', 'book1'], ['book1', 'book20', 'book2'], list(catalog.BOOKS)]
            self.assertEqual([catalog.total_for_items(ids, config) for ids in selections], expected)

    def test_invalid_or_duplicate_books_are_not_priced(self):
        config = localization.pricing_for_country(catalog.PRICES, 'EG')
        for ids in [['book20', 'book20'], ['book999'], ['book1', 'book999']]:
            with self.assertRaises(ValueError):
                catalog.total_for_items(ids, config)

    def test_public_policy_keeps_all_ordinary_books_unchanged(self):
        for country, fixed in [('EG', 500), ('KW', 19.99)]:
            config = localization.pricing_for_country(catalog.PRICES, country)
            self.assertEqual(config['bookPolicies']['book20'], {'price': fixed, 'bundleEligible': False, 'previewEnabled': False})
            self.assertEqual(config['bookPolicies']['book19'], {'price': config['prices']['1'], 'bundleEligible': True, 'previewEnabled': True})
            self.assertNotIn('20', config['prices'])


class NoSigmaPreviewHTTPTests(HTTPCase):
    def test_all_sigma_preview_routes_deny_access_before_reading_the_original(self):
        with patch.object(app.store, 'original') as original:
            for book_id in ('book20', 'book21', 'book22'):
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
