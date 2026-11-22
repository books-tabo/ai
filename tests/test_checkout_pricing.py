"""Synthetic HTTP tests: no real charge, buyer or download entitlement."""
import copy
import json
import os
import time
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

import app
import checkout_pricing as fx
from support import HTTPCase, kashier_payload
from test_kashier import FakeResponse

KEY = 'synthetic-session-binding-key-32-bytes'
REAL_INSERT = app.store.insert


class QuoteTests(unittest.TestCase):
    def test_signed_quote_rounding_binding_expiry_and_tampering(self):
        with patch.object(fx, 'usd_egp_rate', return_value=(Decimal('54.16975051'), '2026-10-20')):
            q = fx.issue(['book2', 'book1'], 'SA', 17.99, 'live', KEY)
        self.assertEqual(q['chargeTotal'], '974.51')
        args = (q['token'], ['book1', 'book2'], 'SA', 17.99, 'live', KEY)
        self.assertEqual(fx.verify(*args)['rate'], q['rate'])
        variants = [('', *args[1:]), (q['token']+'x', *args[1:]),
                    (q['token'], ['book3', 'book2'], *args[2:]),
                    (*args[:2], 'AE', *args[3:]), (*args[:3], 9.99, *args[4:]),
                    (*args[:4], 'test', KEY), (*args[:-1], KEY+'different')]
        for changed in variants:
            with self.assertRaises(fx.InvalidQuote):
                fx.verify(*changed)
        with patch.object(fx.time, 'time', return_value=q['expiresAt']):
            with self.assertRaises(fx.InvalidQuote):
                fx.verify(*args)

    def test_rate_lookup_is_bounded_cached_and_rejects_invalid_or_stale_data(self):
        current = time.time()
        data = {'success': True, 'base': 'USD', 'date': time.strftime('%Y-%m-%d', time.gmtime(current)),
                'timestamp': int(current*1000), 'rates': {'EGP': 54.16975051}}
        self.addCleanup(setattr, fx, '_rate_cache', None)
        fx._rate_cache = None
        with patch.object(fx, 'urlopen', return_value=FakeResponse(data)) as fetch:
            self.assertEqual(fx.usd_egp_rate()[0], Decimal('54.16975051'))
            fx.usd_egp_rate()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(fetch.call_args.kwargs['timeout'], 8)
            self.assertEqual(fetch.call_args.args[0].headers, {'Accept': 'application/json'})
        for changes in ({'timestamp': 0}, {'base': 'EGP'}, {'success': False},
                        {'rates': {'EGP': 'NaN'}}, {'rates': {'EGP': -1}},
                        {'timestamp': int((current+3600)*1000)}):
            fx._rate_cache = None
            with patch.object(fx, 'urlopen', return_value=FakeResponse({**data, **changes})):
                with self.assertRaises(fx.RateUnavailable):
                    fx.usd_egp_rate()
        fx._rate_cache = None
        with patch.object(fx, 'urlopen', side_effect=TimeoutError('private network detail')):
            with self.assertRaises(fx.RateUnavailable) as caught:
                fx.usd_egp_rate()
            self.assertNotIn('private', str(caught.exception))
        with patch.object(fx, '_rate_lock') as lock:
            lock.acquire.return_value=False
            with self.assertRaises(fx.RateUnavailable):
                fx.usd_egp_rate()
            lock.acquire.assert_called_once_with(timeout=0.1)


class RegionalPricingHTTPTests(HTTPCase):
    def setUp(self):
        super().setUp()
        os.environ['TABO_PUBLIC_URL']='https://tabobooks.com'
        self.install()
        for target, name, result in [(app, 'checkout_ready', True), (app, 'paypal_ready', True),
                                     (fx, 'usd_egp_rate', (Decimal('54.16975051'), '2026-10-20'))]:
            p=patch.object(target, name, return_value=result)
            p.start(); self.addCleanup(p.stop)
        def insert(o):
            self.db.orders[o['id']]={**copy.deepcopy(o),'paymobOrderId':None}
        def update(oid, changes, conditions=None):
            result=self.db.patch(oid, changes, conditions)
            if result and 'paymob_order_id' in changes:
                self.db.orders[oid]['paymobOrderId']=changes['paymob_order_id']
                result=copy.deepcopy(self.db.orders[oid])
            return result
        p=patch.object(app.store, 'insert', side_effect=insert)
        self.insert=p.start(); self.addCleanup(p.stop)
        p=patch.object(app.store, 'patch', side_effect=update)
        p.start(); self.addCleanup(p.stop)
        p=patch.object(app.paypal_checkout,'create',return_value=(
            'https://www.sandbox.paypal.com/checkoutnow?token=PAYPALORDER1234567','PAYPALORDER1234567'))
        self.paypal=p.start();self.addCleanup(p.stop)
        p=patch.object(app,'payment_checkout',return_value=(
            'https://payments.kashier.io/session/synthetic-session?mode=test','synthetic-session'))
        self.kashier=p.start();self.addCleanup(p.stop)
        self.items=[{'id':'book1'}]
        self.customer={'name':'Synthetic Buyer','phone':'01000000000','email':'synthetic@example.invalid'}

    def create(self, **extra):
        app.RATE_LIMITER=app.SlidingWindowLimiter()
        status,data,_=self.post('/api/orders',{'items':self.items,'country':'SA',
            'customer':self.customer,'total':1,'currency':'EGP',**extra})
        self.assertEqual(status,201)
        return self.db.orders[data['order']['id']]

    def legacy_quote(self, ids=None, total=4.99):
        return fx.issue(ids or ['book1'],'SA',total,app.payment_mode(),KEY)

    def test_international_orders_charge_exact_usd_without_fx(self):
        with patch.object(fx,'issue',side_effect=AssertionError('no FX quote')), \
             patch.object(fx,'verify',side_effect=AssertionError('no FX conversion')):
            status,data,headers=self.post('/api/checkout-quote',{'items':self.items,'country':'SA'})
            self.assertEqual((status,data['quote']),(200,None))
            self.assertEqual(headers['Cache-Control'],'no-store')
            saved=self.create()
        self.assertEqual((saved['total'],saved['currency'],saved['paymentMethod']),(4.99,'USD','PayPal'))
        self.assertIsNone(saved['pricingQuote'])
        self.assertEqual(self.paypal.call_args.args[0]['total'],4.99)
        self.kashier.assert_not_called()

    def test_legacy_kashier_quote_does_not_bypass_regional_restriction(self):
        q=self.legacy_quote()
        for token in (None,q['token'],q['token']+'x'):
            status,body,_=self.post('/api/orders',{'items':self.items,'country':'SA',
                'customer':self.customer,'paymentProvider':'kashier','quoteToken':token})
            self.assertEqual((status,body['code']),(403,'PAYMENT_PROVIDER_NOT_ALLOWED'))
        self.insert.assert_not_called();self.kashier.assert_not_called();self.paypal.assert_not_called()

    def test_fx_failure_cannot_block_paypal_but_cross_site_requests_are_rejected(self):
        with patch.object(fx,'usd_egp_rate',side_effect=fx.RateUnavailable()):
            self.assertEqual(self.post('/api/checkout-quote',{'items':self.items,'country':'SA'})[0],200)
            self.create()
        count=self.insert.call_count
        self.assertEqual(self.post('/api/checkout-quote',{'items':self.items},
            {'Origin':'https://untrusted.invalid'})[0],403)
        self.assertEqual(self.insert.call_count,count)

    def test_egypt_price_unchanged_and_no_fx_lookup(self):
        with patch.object(app.Handler,'pricing_country',return_value='EG'), \
             patch.object(fx,'usd_egp_rate',side_effect=AssertionError('no FX lookup')):
            self.assertIsNone(self.post('/api/checkout-quote',{'items':self.items})[1]['quote'])
            saved=self.create()
        self.assertEqual((saved['total'],saved['currency'],saved['paymentMethod']),(149.99,'EGP','Kashier'))
        self.assertIsNone(saved['pricingQuote']);self.paypal.assert_not_called()

    def test_every_cart_size_preserves_server_usd_totals(self):
        for count in range(1,len(app.BOOKS)+1):
            self.items=[{'id':bid,'price':0.01} for bid in list(app.BOOKS)[:count]]
            saved=self.create()
            expected=app.catalog.total_for_items([item['id'] for item in self.items],
                app.localization.pricing_for_country(app.PRICES,'SA'))
            self.assertEqual((saved['total'],saved['currency']),(expected,'USD'))
            self.assertEqual(self.paypal.call_args.args[0]['total'],expected)
        self.kashier.assert_not_called()

    def test_former_sigma_titles_participate_in_quantity_discounts(self):
        for ids,total in [(['book20'],4.99),(['book1','book20'],8.98),(['book1','book20','book2'],12.97)]:
            self.items=[{'id':bid,'price':1,'bundleEligible':False} for bid in ids]
            self.assertEqual(self.create()['total'],total)

    def test_sigma_egypt_orders_ignore_client_prices_and_bundle_position(self):
        cases=[(['book20'],149.99),(['book20','book1'],298.98),(['book1','book20','book2'],447.97),(['book21'],149.99),(['book22'],149.99),(['book22','book1','book2'],447.97),(['book20','book21'],279.99),(['book23'],149.99),(['book24'],149.99),(['book24','book23','book22'],409.99),(['book20','book21','book22'],428.98),(['book20','book21','book22','book23'],539.99),(['book25'],149.99),(['book25','book21'],298.98),(list(app.BOOKS),3535.85)]
        with patch.object(app.Handler,'pricing_country',return_value='EG'):
            for ids,total in cases:
                self.items=[{'id':bid,'price':0.01,'bundleEligible':True} for bid in ids]
                saved=self.create()
                self.assertEqual((saved['total'],saved['currency']),(total,'EGP'))
                self.assertEqual(self.kashier.call_args.args[0]['total'],total)
        self.paypal.assert_not_called()

    def test_sigma_usd_orders_keep_fixed_prices_and_pack_discounts(self):
        cases=[(['book20'],4.99),(['book20','book1'],8.98),(['book1','book20','book2'],12.97),(['book21'],4.99),(['book22'],4.99),(['book22','book1','book2'],12.97),(['book20','book21'],7.99),(['book23'],4.99),(['book24'],4.99),(['book24','book23','book22'],10.99),(['book20','book21','book22'],11.98),(['book20','book21','book22','book23'],13.99),(['book25'],4.99),(list(app.BOOKS),90.85)]
        for ids,total in cases:
            self.items=[{'id':bid} for bid in ids]
            saved=self.create()
            self.assertEqual((saved['total'],saved['currency']),(total,'USD'))
            self.assertEqual(self.paypal.call_args.args[0]['total'],total)
            self.assertIsNone(saved['pricingQuote'])
        self.kashier.assert_not_called()

    def test_curated_offer_orders_ignore_client_prices_and_keep_every_book(self):
        for country,currency in [('EG','EGP'),('SA','USD')]:
            for offer in app.catalog.OFFERS.values():
                ids=offer['books'];self.items=[{'id':bid,'price':0.01} for bid in ids]
                with patch.object(app.Handler,'pricing_country',return_value=country):
                    saved=self.create(offerPrice=1)
                self.assertEqual([x['id'] for x in saved['items']],ids)
                self.assertEqual((saved['total'],saved['currency']),(offer['prices'][currency],currency))
                gateway=self.kashier if country=='EG' else self.paypal
                self.assertEqual(gateway.call_args.args[0]['total'],saved['total'])

    def test_removing_a_pack_book_reprices_the_remaining_cart(self):
        for offer_id in ('self-growth','sigma-couple','sigma-trilogy'):
            ids=app.catalog.OFFERS[offer_id]['books'][:-1]
            self.items=[{'id':bid} for bid in ids]
            saved=self.create(offerPrice=1)
            expected=app.catalog.total_for_items(ids,app.localization.pricing_for_country(app.PRICES,'SA'))
            self.assertEqual((saved['total'],saved['currency']),(expected,'USD'))

    def test_old_usd_quote_cannot_authorize_an_old_price_for_paypal(self):
        for ids,old in [(['book20'],15.90),(['book21'],15.99),(['book22'],15.99),
                        (['book20','book21'],20),(['book20','book21','book22','book23'],39)]:
            self.items=[{'id':bid} for bid in ids]
            saved=self.create(quoteToken=self.legacy_quote(ids,old)['token'])
            expected=app.catalog.total_for_items(ids,app.localization.pricing_for_country(app.PRICES,'SA'))
            self.assertEqual((saved['total'],saved['currency']),(expected,'USD'))
            self.assertIsNone(saved['pricingQuote'])

    def test_adapter_preserves_quote_and_legacy_rows(self):
        row={'id':'synthetic','status':'PENDING','items':[],'total':270.31,'currency':'EGP',
             'payment_method':'Kashier','status_key':'synthetic','download_token':None,
             'buyer':None,'transaction_id':None,'paymob_order_id':None}
        q=self.legacy_quote();q.pop('token')
        self.assertIsNone(app.store.order(row)['pricingQuote'])
        self.assertEqual(app.store.order({**row,'pricing_quote':q})['pricingQuote'],q)
        with patch.object(app.store,'request',new=Mock()) as save:
            REAL_INSERT({**app.store.order(row),'pricingQuote':q})
            self.assertEqual(save.call_args.args[2]['pricing_quote'],q)

    def test_existing_converted_order_still_tracks_and_verifies_its_frozen_egp_charge(self):
        from support import order
        saved=order(number=998);q=self.legacy_quote();q.pop('token')
        saved.update(total=float(q['chargeTotal']),pricingQuote=q)
        self.db.orders[saved['id']]=saved
        with self.opener.open(self.base+'/api/order-status?key='+saved['statusKey']) as response:
            tracking=json.load(response)
        self.assertEqual(tracking['pricing'],{'displayCurrency':'USD','displayTotal':'4.99',
            'chargeCurrency':'EGP','chargeTotal':'270.31'})
        self.assertNotIn('token',tracking['pricing']);self.assertNotIn('buyer',tracking)
        wrong,sig=kashier_payload(saved,amount=4.99,currency='USD')
        with patch.object(app,'reconcile_kashier',return_value='paid'):
            self.assertEqual(self.post('/api/kashier/webhook',wrong,sig)[0],400)
            body,sig=kashier_payload(saved)
            self.assertEqual(self.post('/api/kashier/webhook',body,sig)[0],200)
            self.assertEqual(self.post('/api/kashier/webhook',body,sig)[0],200)
        self.assertEqual(len(self.db.events),1)

    def test_existing_usd_order_still_uses_its_original_currency(self):
        from support import order
        saved=order(number=999);saved.update(currency='USD',total=9.99)
        self.db.orders[saved['id']]=saved
        body,sig=kashier_payload(saved)
        with patch.object(app,'reconcile_kashier',return_value='paid'):
            self.assertEqual(self.post('/api/kashier/webhook',body,sig)[0],200)
        self.assertEqual((saved['total'],saved['currency']),(9.99,'USD'))
