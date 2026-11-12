"""Verify scheduled discounts against actual checkout totals in isolation."""
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

import app
import book_promotion
import catalog
import localization
from support import HTTPCase


SALE = {'percent': 20, 'start': 1000, 'end': 2000}
INDIVIDUAL_SALE = {'bookId': 'book21', 'currency': 'USD', 'price': 10,
                   'start': 1000, 'end': 1000 + 7 * 86400}


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

    def test_individual_usd_sale_preserves_prices_and_pack_selection(self):
        with patch.object(book_promotion, 'CAMPAIGN', INDIVIDUAL_SALE):
            for country in ('SA', 'KW', 'EG'):
                before = localization.pricing_for_country(catalog.PRICES, country, at=999)
                active = localization.pricing_for_country(catalog.PRICES, country, at=1000)
                after = localization.pricing_for_country(catalog.PRICES, country, at=INDIVIDUAL_SALE['end'])
                promotion = active.pop('bookPromotion')
                self.assertEqual(promotion, {'bookId': 'book21', 'currency': 'USD', 'price': 10,
                                             'regularPrice': 19.99, 'percent': 50, 'expiresInMs': 604800000})
                self.assertEqual(before, active)
                self.assertEqual(before, after)
                active['bookPromotion'] = promotion
                for ids in (['book20'], ['book20', 'book21'], ['book20', 'book21', 'book22', 'book23'],
                            list(catalog.BOOKS)):
                    self.assertEqual(catalog.pricing_breakdown(ids, active), catalog.pricing_breakdown(ids, before))
                if country == 'SA':
                    self.assertEqual(catalog.total_for_items(['book21'], active), 10)
                    self.assertEqual(catalog.total_for_items(['book21', 'book1'], active), 19.99)
                else:
                    self.assertEqual(catalog.total_for_items(['book21'], active), before['bookPolicies']['book21']['price'])


class PromotionHTTPTests(HTTPCase):
    def test_individual_usd_quote_and_existing_packs_during_the_week(self):
        with patch.object(book_promotion, 'CAMPAIGN', INDIVIDUAL_SALE), \
             patch.object(app.time, 'time', return_value=1000) as clock, \
             patch.object(app, 'checkout_ready', return_value=True), \
             patch.object(app, 'paypal_ready', return_value=True), \
             patch.object(app.checkout_pricing, 'usd_egp_rate', return_value=(Decimal('50'), '2026-11-13')), \
             patch.object(app.store, 'insert') as insert:
            with self.opener.open(self.base + '/api/store-config?country=KW') as response:
                kuwait = json.load(response)
            self.assertEqual(kuwait['paypalPricing']['bookPromotion']['price'], 10)
            with self.opener.open(self.base + '/api/store-config?country=SA') as response:
                config = json.load(response)
            for ids, expected in ((['book21'], '10.00'), (['book20', 'book21'], '34.99'),
                                  (['book20', 'book21', 'book22', 'book23'], '49.00')):
                status, result, _ = self.post('/api/checkout-quote', {
                    'country': 'SA', 'items': [{'id': item} for item in ids],
                    'catalogRevision': config['catalogRevision'],
                })
                self.assertEqual((status, Decimal(result['quote']['displayTotal'])), (200, Decimal(expected)))
            clock.return_value = INDIVIDUAL_SALE['end']
            status, result, _ = self.post('/api/checkout-quote', {'catalogRevision': config['catalogRevision']})
            self.assertEqual((status, result['code']), (409, 'CATALOG_REFRESH_REQUIRED'))
            insert.assert_not_called()

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
