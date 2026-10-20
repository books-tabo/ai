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
            q = fx.issue(['book2', 'book1'], 'KW', 17.99, 'live', KEY)
        self.assertEqual(q['chargeTotal'], '974.51')
        args = (q['token'], ['book1', 'book2'], 'KW', 17.99, 'live', KEY)
        self.assertEqual(fx.verify(*args)['rate'], q['rate'])
        variants = [('', *args[1:]), (q['token']+'x', *args[1:]),
                    (q['token'], ['book3', 'book2'], *args[2:]),
                    (*args[:2], 'SA', *args[3:]), (*args[:3], 9.99, *args[4:]),
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


class QuoteHTTPTests(HTTPCase):
    def setUp(self):
        super().setUp()
        os.environ['TABO_PUBLIC_URL']='https://tabobooks.com'
        self.install()
        for target, name, result in [(app, 'checkout_ready', True),
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
        self.items=[{'id':'book1'}]
        self.customer={'name':'Synthetic Buyer','phone':'01000000000','email':'synthetic@example.invalid'}

    def quote(self):
        status, data, headers=self.post('/api/checkout-quote', {'items':self.items,'country':'KW'})
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        return data['quote']

    def test_usd_quote_is_frozen_and_provider_and_webhook_use_egp(self):
        q=self.quote()
        reply={'_id':'synthetic-session','sessionUrl':'https://payments.kashier.io/session/synthetic-session?mode=test'}
        captured=[]
        def gateway(req, timeout):
            captured.append(json.loads(req.data))
            return FakeResponse(reply)
        with patch.object(fx, 'usd_egp_rate', side_effect=AssertionError('reuse quote')), patch.object(app, 'urlopen', side_effect=gateway):
            status, data, _=self.post('/api/orders', {'items':self.items,'country':'KW',
                'customer':self.customer,'quoteToken':q['token'],'total':1,'currency':'USD'})
        self.assertEqual(status, 201)
        self.assertEqual((data['order']['total'],data['order']['currency']), (541.16,'EGP'))
        self.assertEqual((captured[0]['amount'],captured[0]['currency']), ('541.16','EGP'))
        stored=self.db.orders[data['order']['id']]
        self.assertEqual(stored['pricingQuote']['displayTotal'], '9.99')
        self.assertEqual(stored['pricingQuote']['rate'], '54.16975051')
        self.assertNotIn('token', stored['pricingQuote'])
        wrong, sig=kashier_payload(stored, amount=9.99, currency='USD')
        with patch.object(app, 'reconcile_kashier', return_value='paid'):
            self.assertEqual(self.post('/api/kashier/webhook',wrong,sig)[0], 400)
            body,sig=kashier_payload(stored)
            self.assertEqual(self.post('/api/kashier/webhook',body,sig)[0], 200)
            self.assertEqual(self.post('/api/kashier/webhook',body,sig)[0], 200)
        self.assertEqual(len(self.db.events), 1)

    def test_missing_changed_or_expired_quote_cannot_create_order(self):
        q=self.quote()
        body={'items':self.items,'country':'KW','customer':self.customer}
        self.assertEqual(self.post('/api/orders',body)[0], 409)
        self.assertEqual(self.post('/api/orders',{**body,'quoteToken':q['token']+'x'})[0], 409)
        self.assertEqual(self.post('/api/orders',{**body,'quoteToken':q['token'],'items':[{'id':'book2'}]})[0], 409)
        with patch.object(fx.time, 'time', return_value=q['expiresAt']):
            status,data,_=self.post('/api/orders',{**body,'quoteToken':q['token']})
            self.assertEqual((status,data['code']), (409,'QUOTE_REFRESH_REQUIRED'))
        self.insert.assert_not_called()

    def test_rate_failure_and_cross_site_quote_never_start_payment(self):
        with patch.object(fx, 'usd_egp_rate', side_effect=fx.RateUnavailable()):
            self.assertEqual(self.post('/api/checkout-quote',{'items':self.items,'country':'KW'})[0], 503)
        self.assertEqual(self.post('/api/checkout-quote',{'items':self.items},{'Origin':'https://untrusted.invalid'})[0], 403)
        self.insert.assert_not_called()

    def test_egypt_price_unchanged_and_no_fx_lookup(self):
        with patch.object(app.Handler, 'pricing_country', return_value='EG'), patch.object(fx, 'usd_egp_rate', side_effect=AssertionError()), patch.object(app, 'payment_checkout', return_value=('https://payments.kashier.io/session/synthetic-session?mode=test','synthetic-session')):
            self.assertIsNone(self.post('/api/checkout-quote',{'items':self.items})[1]['quote'])
            status,data,_=self.post('/api/orders',{'items':self.items,'customer':self.customer})
        self.assertEqual(status, 201)
        self.assertEqual((data['order']['total'],data['order']['currency']), (199,'EGP'))
        self.assertIsNone(self.db.orders[data['order']['id']]['pricingQuote'])

    def test_all_18_bundle_quotes_preserve_usd_prices(self):
        for count in range(1,19):
            self.items=[{'id':'book'+str(i)} for i in range(1,count+1)]
            q=self.quote()
            self.assertEqual(Decimal(q['displayTotal']), Decimal(str(app.localization.USD_PRICES[str(count)])))
            self.assertEqual(q['chargeCurrency'], 'EGP')

    def test_adapter_preserves_quote_and_legacy_rows(self):
        row={'id':'synthetic','status':'PENDING','items':[],'total':541.16,'currency':'EGP',
             'payment_method':'Kashier','status_key':'synthetic','download_token':None,
             'buyer':None,'transaction_id':None,'paymob_order_id':None}
        q=self.quote(); q.pop('token')
        self.assertIsNone(app.store.order(row)['pricingQuote'])
        self.assertEqual(app.store.order({**row,'pricing_quote':q})['pricingQuote'], q)
        with patch.object(app.store, 'request', new=Mock()) as save:
            REAL_INSERT({**app.store.order(row),'pricingQuote':q})
            self.assertEqual(save.call_args.args[2]['pricing_quote'], q)

    def test_existing_usd_order_still_uses_its_original_currency(self):
        from support import order
        o=order(number=999)
        o.update(currency='USD',total=9.99)
        self.db.orders[o['id']]=o
        body,sig=kashier_payload(o)
        with patch.object(app,'reconcile_kashier',return_value='paid'):
            self.assertEqual(self.post('/api/kashier/webhook',body,sig)[0],200)
        self.assertEqual((o['total'],o['currency']),(9.99,'USD'))
