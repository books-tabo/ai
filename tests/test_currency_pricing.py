"""International USD and Egyptian EGP pricing with synthetic checkout fixtures."""
import base64
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


class CurrencyPricingTests(unittest.TestCase):
    def test_kuwait_uses_the_same_book_and_pack_prices_as_other_usd_markets(self):
        kuwait = localization.pricing_for_country(catalog.PRICES, 'KW', at=0)
        international = localization.pricing_for_country(catalog.PRICES, 'SA', at=0)
        egypt = localization.pricing_for_country(catalog.PRICES, 'EG', at=0)
        self.assertEqual(kuwait['currency'], 'USD')
        self.assertEqual({item['currency'] for item in kuwait['countries']}, {'EGP', 'USD'})
        for key in ('prices', 'compareAtPrice', 'bookPolicies', 'offers'):
            self.assertEqual(kuwait[key], international[key])
        self.assertEqual(egypt['prices'], {str(count): price for count, price in catalog.PRICES.items()})
        self.assertEqual(egypt['bookPolicies']['book21']['price'], 500)
        self.assertEqual(catalog.total_for_items(['book20', 'book21'], kuwait), 34.99)
        self.assertEqual(catalog.total_for_items(['book20', 'book21', 'book22', 'book23'], kuwait), 49)

    def test_legacy_kwd_quote_is_rejected_for_new_checkout(self):
        key = 'synthetic-session-binding-key-32-bytes'
        args = (['book20'], 'KW', 6.2, 'test', key)
        with patch.object(fx, 'usd_egp_rate', return_value=(Decimal('170'), '2026-11-05')):
            old = fx.issue(*args)
        del old['token']
        old['displayCurrency'] = 'KWD'
        encoded = base64.urlsafe_b64encode(json.dumps(old).encode()).decode().rstrip('=')
        token = encoded + '.' + fx._sign(encoded, key)
        with self.assertRaises(fx.InvalidQuote):
            fx.verify(token, *args)


class CurrencyPricingHTTPTests(HTTPCase):
    def setUp(self):
        super().setUp()
        self.install()
        os.environ['TABO_PUBLIC_URL']='https://tabobooks.com'
        for target,name,result in ((app,'checkout_ready',True),
                                   (fx,'usd_egp_rate',(Decimal('50'),'2026-11-13'))):
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

    def config(self, country):
        with self.opener.open(self.base+'/api/store-config?country='+country) as response:
            return json.load(response)

    def test_international_currency_and_egypt_with_a_saved_kuwait_selection(self):
        cfg = self.config('KW')
        self.assertEqual((cfg['country'], cfg['currency']), ('KW', 'USD'))
        self.assertEqual(cfg['chargeCurrency'],'EGP')
        self.assertTrue(cfg['checkoutSupported'])
        for country in ('EG', 'SA', 'US', 'AU', 'BR'):
            self.assertEqual(self.config(country)['currency'], 'USD')
        with patch.object(app.Handler, 'client_id', return_value='41.32.1.1'):
            egypt = self.config('KW')
            self.assertEqual((egypt['country'], egypt['currency']), ('EG', 'EGP'))
            self.assertEqual(egypt['bookPolicies']['book21']['price'], 500)
            status, reply, _ = self.post('/api/checkout-quote', {
                'country': 'KW', 'items': [{'id': 'book21'}], 'catalogRevision': cfg['catalogRevision'],
            })
            self.assertEqual((status, reply['code']), (409, 'CATALOG_REFRESH_REQUIRED'))

    def test_kashier_kuwait_order_displays_usd_and_charges_quoted_egp(self):
        items=[{'id':'book20'}]
        cart = {'country': 'KW', 'items': items, 'catalogRevision': self.config('KW')['catalogRevision']}
        status,reply,_=self.post('/api/checkout-quote', cart)
        self.assertEqual(status,200)
        q=reply['quote']
        self.assertEqual((q['displayCurrency'],q['chargeCurrency'],q['chargeTotal']),('USD','EGP','999.50'))
        gateway_response={'_id':'synthetic-session','sessionUrl':'https://payments.kashier.io/session/synthetic-session?mode=test'}
        captured=[]
        def gateway(request, timeout):
            captured.append(json.loads(request.data))
            return FakeResponse(gateway_response)
        with patch.object(fx,'usd_egp_rate',side_effect=AssertionError('quote must be reused')), \
             patch.object(app,'urlopen',side_effect=gateway):
            promoter_code = '1234567890abcdef'
            referral = app.owner_dashboard.promoter_cookie(promoter_code)
            status,data,_=self.post('/api/orders',{**cart,
                'customer':self.customer,'quoteToken':q['token'],'promoterCode':'f'*16},
                {'Cookie': app.owner_dashboard.PROMOTER_COOKIE + '=' + referral})
        self.assertEqual(status,201)
        self.assertEqual((data['order']['currency'],data['order']['total']),('EGP',999.50))
        self.assertEqual((captured[0]['currency'],captured[0]['amount']),('EGP','999.50'))
        self.assertEqual(captured[0]['metaData']['displayNotes'],{
            'سعر الطلب بالدولار الأمريكي':'19.99 USD',
            'المبلغ المحصل بالجنيه المصري':'999.50 EGP'})
        stored=self.db.orders[data['order']['id']]
        self.assertEqual(stored['promoterCode'], promoter_code)
        self.assertNotIn('promoterCode', data['order'])
        self.assertEqual(stored['pricingQuote']['displayCurrency'],'USD')
        self.assertNotIn('token',stored['pricingQuote'])
        with self.opener.open(self.base+'/api/order-status?key='+stored['statusKey']) as response:
            status_payload=json.load(response)
        self.assertEqual(status_payload['pricing'],{
            'displayCurrency':'USD','displayTotal':'19.99',
            'chargeCurrency':'EGP','chargeTotal':'999.50'})

    def test_stale_kuwait_cart_requires_review_before_payment(self):
        current = self.config('KW')['catalogRevision']
        previous = current.split(':egp-usd:', 1)[0]
        with patch.object(app,'payment_checkout') as gateway:
            for route in ('/api/checkout-quote', '/api/orders'):
                status, reply, _ = self.post(route, {'country': 'KW', 'items': [{'id': 'book21'}],
                    'customer': self.customer, 'catalogRevision': previous})
                self.assertEqual((status, reply['code']), (409, 'CATALOG_REFRESH_REQUIRED'))
            gateway.assert_not_called()
