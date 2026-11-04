"""Kuwaiti dinar prices and synthetic KWD-display / EGP-charge checkout tests."""
import copy
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

import app
import catalog
import checkout_pricing as fx
import localization
from support import HTTPCase
from test_kashier import FakeResponse


class KuwaitiDinarPricingTests(unittest.TestCase):
    def test_approved_kuwait_ladder_and_untouched_other_currencies(self):
        kw = localization.pricing_for_country(catalog.PRICES, 'KW')
        eg = localization.pricing_for_country(catalog.PRICES, 'EG')
        us = localization.pricing_for_country(catalog.PRICES, 'SA')
        self.assertEqual((kw['currency'],eg['currency'],us['currency']), ('KWD','EGP','USD'))
        self.assertEqual(kw['compareAtPrice'], 4.650)
        expected = [3.100,5.600,7.750,9.300,10.900,12.450,14.000,
                    15.550,17.100,18.650,20.200,21.750,23.300,24.850,
                    26.400,27.950,29.500,31.100,32.650]
        for count, amount in enumerate(expected, 1):
            # Curated packs can legitimately discount the total below the
            # ordinary quantity ladder. Compare the unchanged base schedules.
            self.assertEqual(kw['prices'][str(count)], amount)
            self.assertEqual(eg['prices'][str(count)], catalog.PRICES[count])
            self.assertEqual(us['prices'][str(count)], localization.USD_PRICES[str(count)])
        self.assertEqual(catalog.total_for_items(['book1'], kw), 3.100)
        self.assertEqual(next(c for c in kw['countries'] if c['code']=='KW')['currency'],'KWD')
        self.assertEqual(next(c for c in kw['countries'] if c['code']=='EG')['currency'],'EGP')

    def test_special_books_and_offers_use_approved_kuwait_prices(self):
        kw = localization.pricing_for_country(catalog.PRICES, 'KW')
        for book_id in ('book20','book21','book22','book23','book24'):
            self.assertEqual(kw['bookPolicies'][book_id]['price'], 6.200)
            self.assertFalse(kw['bookPolicies'][book_id]['bundleEligible'])
        for offer_id, expected in [('change-starter',4.650),('money-and-wealth',6.200),
                                   ('self-growth',7.150),('sigma-couple',10.900),
                                   ('sigma-trilogy',15.250),('marriage-awareness',12.100)]:
            offer = catalog.OFFERS[offer_id]
            self.assertEqual(catalog.total_for_items(offer['books'],kw), expected)
            self.assertEqual(next(x for x in kw['offers'] if x['id']==offer_id)['price'],expected)
        self.assertEqual(catalog.total_for_items(['book20','book1'],kw),9.300)
        self.assertEqual(catalog.total_for_items(['book20','book21'],kw),10.900)

    def test_signed_kwd_quote_is_currency_bound(self):
        key = 'synthetic-session-binding-key-32-bytes'
        with patch.object(fx,'kwd_egp_rate',return_value=(Decimal('170.0000'),'2026-11-05')):
            q=fx.issue(['book20'],'KW',6.2,'test',key,display_currency='KWD')
        self.assertEqual((q['displayCurrency'],q['chargeCurrency'],q['chargeTotal']),('KWD','EGP','1054.00'))
        args=(q['token'],['book20'],'KW',6.2,'test',key)
        self.assertEqual(fx.verify(*args,display_currency='KWD')['displayTotal'],'6.2')
        with self.assertRaises(fx.InvalidQuote):fx.verify(*args,display_currency='USD')
        with self.assertRaises(fx.InvalidQuote):fx.verify(q['token'],['book20'],'SA',6.2,'test',key,display_currency='KWD')
        with self.assertRaises(fx.InvalidQuote):fx.verify(q['token'],['book20'],'KW',6.25,'test',key,display_currency='KWD')


class KuwaitiDinarHTTPTests(HTTPCase):
    def setUp(self):
        super().setUp()
        self.install()
        os.environ['TABO_PUBLIC_URL']='https://tabobooks.com'
        for target,name,result in ((app,'checkout_ready',True),
                                   (fx,'kwd_egp_rate',(Decimal('170.0000'),'2026-11-05'))):
            p=patch.object(target,name,return_value=result)
            p.start();self.addCleanup(p.stop)
        def insert(order):
            self.db.orders[order['id']]={**copy.deepcopy(order),'paymobOrderId':None}
        def update(oid,changes,conditions=None):
            result=self.db.patch(oid,changes,conditions)
            if result and 'paymob_order_id' in changes:
                self.db.orders[oid]['paymobOrderId']=changes['paymob_order_id']
                result=copy.deepcopy(self.db.orders[oid])
            return result
        p=patch.object(app.store,'insert',side_effect=insert);p.start();self.addCleanup(p.stop)
        p=patch.object(app.store,'patch',side_effect=update);p.start();self.addCleanup(p.stop)
        self.customer={'name':'Synthetic Kuwait Buyer','email':'buyer@example.invalid','phone':'+96598726788'}

    def test_kuwait_config_and_real_charge_currency(self):
        with self.opener.open(self.base+'/api/store-config?country=KW') as response:
            cfg=json.load(response)
        self.assertEqual(cfg['currency'],'KWD')
        self.assertEqual(cfg['chargeCurrency'],'EGP')
        self.assertTrue(cfg['checkoutSupported'])
        self.assertFalse(cfg['paypalAvailable'])
        with self.opener.open(self.base+'/api/store-config?country=SA') as response:
            usd=json.load(response)
        self.assertEqual(usd['currency'],'USD')

    def test_kashier_kwd_order_is_egp_charged_and_display_metadata_preserved(self):
        items=[{'id':'book20'}]
        status,reply,_=self.post('/api/checkout-quote',{'country':'KW','items':items})
        self.assertEqual(status,200)
        q=reply['quote']
        self.assertEqual((q['displayCurrency'],q['chargeCurrency'],q['chargeTotal']),('KWD','EGP','1054.00'))
        gateway_response={'_id':'synthetic-session','sessionUrl':'https://payments.kashier.io/session/synthetic-session?mode=test'}
        captured=[]
        def gateway(request, timeout):
            captured.append(json.loads(request.data))
            return FakeResponse(gateway_response)
        with patch.object(fx,'kwd_egp_rate',side_effect=AssertionError('quote must be reused')), \
             patch.object(app,'urlopen',side_effect=gateway):
            status,data,_=self.post('/api/orders',{'country':'KW','items':items,
                'customer':self.customer,'quoteToken':q['token']})
        self.assertEqual(status,201)
        self.assertEqual((data['order']['currency'],data['order']['total']),('EGP',1054))
        self.assertEqual((captured[0]['currency'],captured[0]['amount']),('EGP','1054.00'))
        self.assertEqual(captured[0]['metaData']['displayNotes'],{
            'سعر الطلب بالدينار الكويتي':'6.200 KWD',
            'المبلغ المحصل بالجنيه المصري':'1054.00 EGP'})
        stored=self.db.orders[data['order']['id']]
        self.assertEqual(stored['pricingQuote']['displayCurrency'],'KWD')
        self.assertNotIn('token',stored['pricingQuote'])
        with self.opener.open(self.base+'/api/order-status?key='+stored['statusKey']) as response:
            status_payload=json.load(response)
        self.assertEqual(status_payload['pricing'],{
            'displayCurrency':'KWD','displayTotal':'6.200',
            'chargeCurrency':'EGP','chargeTotal':'1054.00'})

    def test_foreign_quote_is_required_and_cannot_be_reused_for_usd(self):
        items=[{'id':'book1'}]
        country='KW'
        self.assertEqual(self.post('/api/orders',{'country':country,'items':items,'customer':self.customer})[0],409)
        _,result,_=self.post('/api/checkout-quote',{'country':country,'items':items})
        q=result['quote']
        with patch.object(app,'payment_checkout') as gateway:
            status,reply,_=self.post('/api/orders',{'country':country,'items':[{'id':'book2'}],
                'customer':self.customer,'quoteToken':q['token']})
            self.assertEqual((status,reply['code']),(409,'QUOTE_REFRESH_REQUIRED'))
            gateway.assert_not_called()
