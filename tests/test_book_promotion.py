"""Uniform prices retire historical campaigns; expiry still invalidates carts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app
import book_promotion
import catalog
import localization
from support import HTTPCase

SALE={'percent':20,'start':1000,'end':2000}

class PromotionPricingTests(unittest.TestCase):
    def test_checked_in_campaigns_are_retired_in_both_currencies(self):
        path=Path(book_promotion.__file__).with_name('content')/'book-promotion.json'
        self.assertEqual(book_promotion.load_campaign(path),())
        for country,price in [('EG',149.99),('SA',4.99),('KW',4.99)]:
            for at in (0,1795132860,1795603680,2000000000):
                config=localization.pricing_for_country(catalog.PRICES,country,at=at)
                self.assertNotIn('bookPromotion',config)
                self.assertTrue(all(policy['price']==price for policy in config['bookPolicies'].values()))

    def test_legacy_individual_campaigns_cannot_override_lower_uniform_prices(self):
        for campaign in ({'bookId':'book21','currency':'USD','price':10,'start':1000,'end':2000},
                         {'bookId':'book21','currency':'EGP','price':299,'start':1000,'end':2000}):
            with patch.object(book_promotion,'CAMPAIGN',campaign):
                for country in ('EG','SA'):
                    before=localization.pricing_for_country(catalog.PRICES,country,at=999)
                    self.assertEqual(before,localization.pricing_for_country(catalog.PRICES,country,at=1000))
                    self.assertEqual(before,localization.pricing_for_country(catalog.PRICES,country,at=2000))

    def test_storewide_campaign_math_keeps_the_cheaper_cart_or_pack_total(self):
        with patch.object(book_promotion,'CAMPAIGN',SALE):
            for country,regular,sale in [('EG',149.99,119.99),('SA',4.99,3.99)]:
                config=localization.pricing_for_country(catalog.PRICES,country,at=1000)
                self.assertEqual(config['compareAtPrice'],regular)
                self.assertEqual(config['bookPromotion']['percent'],20)
                self.assertTrue(all(p['price']==sale for p in config['bookPolicies'].values()))
                self.assertEqual(catalog.total_for_items(['book20'],config),sale)
                self.assertEqual(catalog.total_for_items(['book1','book2'],config),239.98 if country=='EG' else 7.98)
            self.assertEqual(book_promotion.revision(0,999,'USD'),0)
            self.assertNotEqual(book_promotion.revision(0,1000,'USD'),0)
            self.assertEqual(book_promotion.revision(0,2000,'USD'),0)

    def test_campaign_validation_rejects_duplicate_currency_and_invalid_periods(self):
        data={'bookId':'book21','currency':'USD','price':1.99,
              'startsAt':'2026-11-21T00:00:00+03:00','endsAt':'2026-11-28T00:00:00+03:00'}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'campaigns.json';path.write_text(json.dumps([data,data]))
            with self.assertRaises(ValueError):book_promotion.load_campaign(path)
        for bad in ({**data,'price':float('nan')},{**data,'endsAt':data['startsAt']},
                    {**data,'startsAt':'2026-11-21T00:00:00'}):
            with self.assertRaises(ValueError):book_promotion.validate_campaign(bad)

class PromotionHTTPTests(HTTPCase):
    def test_previous_pricing_revision_is_rejected_before_any_new_order_or_quote(self):
        current=app.checkout_catalog_revision('SA',0)
        self.assertIn(catalog.PRICING_VERSION,current)
        with patch.object(app.store,'insert') as insert,patch.object(app,'payment_checkout') as pay:
            for route in ('/api/orders','/api/checkout-quote'):
                for revision in ('0:egp-usd:USD','0:egp-usd:EGP'):
                    status,body,_=self.post(route,{'country':'SA','catalogRevision':revision})
                    self.assertEqual((status,body['code']),(409,'CATALOG_REFRESH_REQUIRED'))
            insert.assert_not_called();pay.assert_not_called()

    def test_expired_campaign_requires_review_before_order_creation(self):
        with patch.object(book_promotion,'CAMPAIGN',SALE):
            with patch.object(app.time,'time',return_value=1000):
                with self.opener.open(self.base+'/api/store-config?country=KW') as response:config=json.load(response)
                self.assertEqual(config['bookPolicies']['book21']['price'],3.99)
                self.assertIn(':promotion:',config['catalogRevision'])
            with patch.object(app.time,'time',return_value=2000),patch.object(app.store,'insert') as insert:
                for route in ('/api/orders','/api/checkout-quote'):
                    status,result,_=self.post(route,{'country':'KW','catalogRevision':config['catalogRevision']})
                    self.assertEqual((status,result['code']),(409,'CATALOG_REFRESH_REQUIRED'))
                insert.assert_not_called()
                with self.opener.open(self.base+'/api/store-config?country=KW') as response:current=json.load(response)
                self.assertNotIn('bookPromotion',current)
                self.assertEqual(current['bookPolicies']['book21']['price'],4.99)
