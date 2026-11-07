"""Verify scheduled discounts against actual checkout totals in isolation."""
import json
import unittest
from unittest.mock import patch

import app
import book_promotion
import catalog
import localization
from support import HTTPCase


SALE = {'percent': 20, 'start': 1000, 'end': 2000}


class PromotionPricingTests(unittest.TestCase):
    def test_sale_matches_single_book_checkout_prices_in_each_currency(self):
        examples = {
            'EG': (199, 159.20, 500, 400),
            'SA': (9.99, 7.99, 19.99, 15.99),
            'KW': (3.100, 2.480, 6.200, 4.960),
        }
        with patch.object(book_promotion, 'CAMPAIGN', SALE):
            for country, (regular, sale, fixed_regular, fixed_sale) in examples.items():
                with self.subTest(country=country):
                    before = localization.pricing_for_country(catalog.PRICES, country, at=999)
                    active = localization.pricing_for_country(catalog.PRICES, country, at=1000)
                    after = localization.pricing_for_country(catalog.PRICES, country, at=2000)
                    self.assertEqual(before, after)
                    self.assertEqual(active['compareAtPrice'], regular)
                    self.assertEqual(active['bookPromotion']['percent'], 20)
                    for book_id, policy in active['bookPolicies'].items():
                        expected = (regular, sale) if policy['bundleEligible'] else (fixed_regular, fixed_sale)
                        self.assertEqual((policy['regularPrice'], policy['price']), expected)
                        self.assertEqual(catalog.total_for_items([book_id], active), policy['price'])

    def test_cart_keeps_the_cheaper_existing_pack_or_sale_total(self):
        with patch.object(book_promotion, 'CAMPAIGN', SALE):
            active = localization.pricing_for_country(catalog.PRICES, 'EG', at=1000)
            self.assertEqual(catalog.total_for_items(['book1', 'book2'], active), 299)
            active = localization.pricing_for_country(catalog.PRICES, 'SA', at=1000)
            self.assertEqual(catalog.total_for_items(['book20', 'book21'], active), 31.98)
        with patch.object(book_promotion, 'CAMPAIGN', {**SALE, 'percent': 15}):
            kwd = localization.pricing_for_country(catalog.PRICES, 'KW', at=1000)
            self.assertEqual(kwd['bookPolicies']['book1']['price'], 2.635)
            self.assertEqual(catalog.total_for_items(['book1'], kwd), 2.635)


class PromotionHTTPTests(HTTPCase):
    def test_expired_discount_requires_price_review_before_order_creation(self):
        with patch.object(book_promotion, 'CAMPAIGN', SALE):
            with patch.object(app.time, 'time', return_value=1000):
                with self.opener.open(self.base + '/api/store-config?country=KW') as response:
                    config = json.load(response)
                self.assertEqual(config['bookPolicies']['book21']['price'], 4.960)
                self.assertIn(':promotion:', config['catalogRevision'])
            with patch.object(app.time, 'time', return_value=2000), patch.object(app.store, 'insert') as insert:
                for route in ('/api/orders', '/api/checkout-quote'):
                    status, result, _ = self.post(route, {'catalogRevision': config['catalogRevision']})
                    self.assertEqual((status, result['code']), (409, 'CATALOG_REFRESH_REQUIRED'))
                insert.assert_not_called()
                with self.opener.open(self.base + '/api/store-config?country=KW') as response:
                    current = json.load(response)
                self.assertNotIn('bookPromotion', current)
                self.assertEqual(current['bookPolicies']['book21']['price'], 6.200)
