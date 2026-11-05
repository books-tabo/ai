"""Synthetic, intercepted CAPI regressions. Never send real conversions."""
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from unittest.mock import patch

import app
import meta_capi as capi
import meta_tracking
from support import HTTPCase, order

CONFIG = {'TABO_META_CAPI_ENABLED': '1', 'TABO_META_CAPI_PIXEL_ID': '1390132816288252',
          'TABO_META_ADDITIONAL_PIXEL_ID': '1390132816288252',
          'TABO_META_ADDITIONAL_PURCHASE_SINCE': '2026-11-05T20:17:00Z',
          'TABO_META_CAPI_ACCESS_TOKEN': 'synthetic-token-never-valid-' * 3,
          'TABO_PUBLIC_URL': 'https://tabobooks.com', 'TABO_SITE_ENV': 'production',
          'SUPABASE_URL': 'https://synthetic.invalid', 'SUPABASE_SECRET_KEY': 'synthetic'}
MATCH = {'fbp': 'fb.1.1793910000000.1234567890', 'client_user_agent': 'SyntheticBrowser/1'}


class CAPITests(unittest.TestCase):
    def setUp(self):
        p = patch.dict(os.environ, CONFIG, clear=True); p.start(); self.addCleanup(p.stop)

    def paid(self):
        value = order()
        value.update(status='PAID', paymentMode='live', currency='EGP', total=1090.05,
                     transactionId='SYNTHETIC-TX', paidAt=datetime.now(timezone.utc).isoformat(),
                     pricingQuote={'displayCurrency':'KWD', 'displayTotal':'6.200',
                                   'chargeCurrency':'EGP', 'chargeTotal':'1090.05'})
        return value

    def test_scope_disable_review_and_no_secret_in_public_identifiers(self):
        self.assertTrue(capi.enabled())
        for changes in ({'TABO_META_CAPI_ENABLED':'0'}, {'TABO_SITE_ENV':'review'}, {'TABO_SITE_ENV':'staging'},
                        {'TABO_META_CAPI_PIXEL_ID':'123456789'}, {'TABO_META_CAPI_ACCESS_TOKEN':''},
                        {'TABO_PUBLIC_URL':'https://preview.invalid'}):
            with patch.dict(os.environ, changes), patch.object(capi.store, 'request') as request:
                self.assertFalse(capi.process_one()); request.assert_not_called()

    def test_matching_filters_private_ips_control_characters_and_cookie_injection(self):
        self.assertIsNone(capi.user_data('127.0.0.1','Synthetic'))
        result = capi.user_data('8.8.8.8','Synthetic', MATCH['fbp'], 'bad-token')
        self.assertEqual(set(result), {'client_ip_address','client_user_agent','fbp'})
        self.assertEqual(capi.user_data('unknown','bad\r\nagent',MATCH['fbp']), {'fbp':MATCH['fbp']})
        self.assertIsNone(capi.user_data('unknown','Synthetic','email@example.invalid'))

    def test_browser_cannot_forge_purchase_checkout_price_or_private_source_url(self):
        data={'event_name':'ViewContent','event_id':'a'*64,'path':'/books/book21',
              'content_ids':['book21'],'value':6.2,'currency':'KWD','email':'do-not-copy@synthetic.invalid'}
        with patch.object(capi.store,'request') as request:
            self.assertTrue(capi.browser_event(data,MATCH,'KW'))
            payload=request.call_args.args[2]
            self.assertEqual(payload['payload']['custom_data']['value'],6.2)
            self.assertNotIn('do-not-copy',json.dumps(payload))
            self.assertEqual(payload['pixel_id'], CONFIG['TABO_META_CAPI_PIXEL_ID'])
            capi.browser_event({**data,'value':0.01},MATCH,'KW')
            self.assertNotIn('value',request.call_args.args[2]['payload']['custom_data'])
            for changes in ({'event_name':'Purchase'}, {'event_name':'InitiateCheckout'},
                            {'path':'/success.html?order=PRIVATE'}, {'event_id':'bad'},
                            {'content_ids':['book1','book1']}, {'content_ids':['book9999']}):
                with self.assertRaises(ValueError): capi.browser_event({**data,**changes},MATCH,'KW')

    def test_checkout_enrolls_only_live_and_purchase_waits_for_payment(self):
        value=self.paid(); value.update(status='PENDING',paidAt=None,transactionId=None)
        with patch.object(capi.store,'request') as request:
            identifier=capi.checkout(value,MATCH)
            self.assertRegex(identifier,r'^[0-9a-f]{64}$')
            request.assert_called_once()
            rows=request.call_args.args[2]
            self.assertEqual([x['event_name'] for x in rows],['InitiateCheckout','Purchase'])
            self.assertEqual(rows[0]['payload']['event_id'],identifier)
            self.assertEqual(rows[0]['payload']['custom_data']['currency'],'EGP')
            self.assertEqual(rows[1]['order_id'],value['id'])
            self.assertIsNone(capi.purchase_event(value))
            request.reset_mock()
            self.assertIsNone(capi.checkout({**value,'paymentMode':'test'},MATCH))
            request.assert_not_called()

    def test_purchase_shares_browser_event_id_actual_charge_time_and_no_buyer_data(self):
        value=self.paid(); value['buyer']['email']='synthetic@example.invalid'
        browser=meta_tracking.purchase(value); server=capi.purchase_event(value)
        self.assertEqual(server['event_id'],browser['eventID'])
        self.assertEqual(server['custom_data']['value'],1090.05)
        self.assertEqual(server['custom_data']['currency'],'EGP')
        self.assertEqual(server['event_time'],int(datetime.fromisoformat(value['paidAt']).timestamp()))
        self.assertEqual(server['event_source_url'],'https://tabobooks.com/success.html')
        for private in (value['id'],value['transactionId'],value['buyer']['name'],value['buyer']['phone'],'synthetic@example.invalid'):
            self.assertNotIn(private,json.dumps(server,ensure_ascii=False))
        for changes in ({'status':'PENDING'},{'status':'REFUNDED'},{'paymentMode':'test'},
                        {'currency':'KWD'},{'paidAt':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()}):
            self.assertIsNone(capi.purchase_event({**value,**changes}))

    def test_transport_test_code_token_body_and_sanitized_failure(self):
        payload=capi.event('PageView','a'*64)
        response=io.BytesIO(b'{"events_received":1,"fbtrace_id":"synthetic-trace"}')
        with patch.object(capi,'OPEN',return_value=response) as transport:
            self.assertEqual(capi.send(payload,MATCH,test_event_code='TEST12345'),'synthetic-trace')
            req=transport.call_args.args[0]; body=json.loads(req.data)
            self.assertEqual(req.full_url,'https://graph.facebook.com/v26.0/1390132816288252/events')
            self.assertEqual(body['test_event_code'],'TEST12345')
            self.assertNotIn(CONFIG['TABO_META_CAPI_ACCESS_TOKEN'],req.full_url)
        for status, retryable in ((400,False),(401,False),(429,True),(503,True)):
            error=HTTPError('https://graph.facebook.com',status,'private body',{},io.BytesIO(b'{"error":{"message":"private buyer token"}}'))
            with patch.object(capi,'OPEN',side_effect=error):
                with self.assertRaises(capi.DeliveryError) as caught:capi.send(payload,MATCH)
            self.assertEqual(caught.exception.retryable,retryable)
            self.assertNotIn('private',str(caught.exception))

    def test_worker_rechecks_payment_and_retries_same_key_without_sending_unpaid(self):
        claim={'event_key':'b'*64,'event_name':'Purchase','order_id':order()['id'],
               'payload':{},'user_data':MATCH,'attempts':1}
        with patch.object(capi.store,'request',side_effect=[claim,True]) as request, \
                patch.object(capi.store,'find',return_value=order()),patch.object(capi,'send') as send:
            self.assertTrue(capi.process_one());send.assert_not_called()
            self.assertEqual(request.call_args.args[2]['p_state'],'skipped')
        with patch.object(capi.store,'request',side_effect=[claim,True]) as request, \
                patch.object(capi.store,'find',return_value=self.paid()), \
                patch.object(capi,'send',side_effect=capi.DeliveryError(True)):
            self.assertTrue(capi.process_one())
            self.assertEqual(request.call_args.args[2]['p_state'],'retry')
            self.assertEqual(request.call_args.args[2]['p_event_key'],'b'*64)


class CAPIHTTPTests(HTTPCase):
    def test_same_origin_guard_purchase_rejection_and_server_price(self):
        with patch.dict(os.environ,CONFIG), patch.object(capi.store,'request') as request:
            headers={'Cookie':'_fbp='+MATCH['fbp']}
            body={'event_name':'PageView','event_id':'a'*64,'path':'/','country':'KW'}
            self.assertEqual(self.post('/api/meta/events',body,{**headers,'Origin':'https://other.invalid'})[0],403)
            self.assertEqual(self.post('/api/meta/events',{**body,'event_name':'Purchase'},headers)[0],400)
            self.assertEqual(self.post('/api/meta/events',body,headers)[0],202)
            self.assertEqual(request.call_args.args[2]['payload']['event_id'],'a'*64)
            self.assertNotIn('access_token',json.dumps(request.call_args.args[2]))

    def test_disabled_endpoint_is_inert(self):
        with patch.object(capi.store,'request') as request:
            self.assertEqual(self.post('/api/meta/events',{'event_name':'Purchase'})[0],202)
            request.assert_not_called()
