"""Independent PayPal fixtures. Never calls a live merchant or creates a payment."""
import copy
import io
import json
import os
import unittest
from unittest.mock import patch,Mock
from email.message import Message
from urllib.error import HTTPError
import app
import paypal_checkout as paypal
from support import HTTPCase

ENV={'PAYPAL_MODE':'test','TABO_ENV':'paypal-sandbox','TABO_PUBLIC_URL':'https://paypal-sandbox.example',
     'TABO_PAYPAL_ENABLED':'1','TABO_PAYPAL_CAPABILITY_VERIFIED':'1','TABO_PAYPAL_SCHEMA_READY':'1',
     'PAYPAL_SANDBOX_CLIENT_ID':'synthetic_client_id_12345','PAYPAL_SANDBOX_CLIENT_SECRET':'synthetic_secret_12345678',
     'PAYPAL_SANDBOX_MERCHANT_ID':'MERCHANT123456','PAYPAL_SANDBOX_WEBHOOK_ID':'WEBHOOK12345678'}
PID='PAYPALORDER1234567';CID='PAYPALCAPTURE12345';OID='TABO-0000000000000002'

def order(**changes):
    return {'id':OID,'status':'PENDING','items':[{'id':'book20'},{'id':'book21'}],
            'total':20,'currency':'USD','country':'KW','paymentMethod':'PayPal','paymentMode':'test',
            'statusKey':'2'*64,'paymobOrderId':PID,'transactionId':None,
            'buyer':{'name':'Synthetic buyer','phone':'01000000000'},
            'checkoutSessionHash':app.hash_secret('synthetic-browser-secret'),**changes}


def remote(status='COMPLETED',capture_status='COMPLETED'):
    return {'id':PID,'status':status,'purchase_units':[{'reference_id':OID,'custom_id':OID,'invoice_id':OID,
       'amount':{'currency_code':'USD','value':'20.00'},'payee':{'merchant_id':'MERCHANT123456'},
       'payments':{'captures':[{'id':CID,'amount':{'currency_code':'USD','value':'20.00'},'final_capture':True}]}}]}


def capture_data(status='COMPLETED'):
    return {'id':CID,'status':status,'custom_id':OID,'amount':{'currency_code':'USD','value':'20.00'},
            'supplementary_data':{'related_ids':{'order_id':PID}}}


class PayPalClientTests(unittest.TestCase):
    def setUp(self):
        p=patch.dict(os.environ,ENV,clear=True);p.start();self.addCleanup(p.stop)

    def test_optional_configuration_fails_closed_and_sandbox_cannot_run_on_production(self):
        self.assertTrue(paypal.available('USD'));self.assertFalse(paypal.available('EGP'))
        for name in ('TABO_PAYPAL_ENABLED','TABO_PAYPAL_CAPABILITY_VERIFIED','TABO_PAYPAL_SCHEMA_READY','PAYPAL_SANDBOX_CLIENT_SECRET'):
            with patch.dict(os.environ,{name:''}):self.assertFalse(paypal.available('USD'))
        for host in ('https://tabobooks.com','https://www.tabobooks.com','https://tabo-books-preview.onrender.com'):
            with patch.dict(os.environ,{'TABO_PUBLIC_URL':host}):self.assertFalse(paypal.configured())
        with patch.dict(os.environ,{'TABO_ENV':'production'}):self.assertFalse(paypal.configured())
        # Disabling new checkouts does not discard callbacks for existing orders.
        with patch.dict(os.environ,{'TABO_PAYPAL_ENABLED':'0'}):
            self.assertFalse(paypal.available('USD'));self.assertTrue(paypal.configured())

    def test_create_is_hosted_usd_no_shipping_and_approval_links_are_allowlisted(self):
        response={'id':PID,'links':[{'rel':'payer-action','href':'https://www.sandbox.paypal.com/checkoutnow?token='+PID}]}
        with patch.object(paypal,'api',return_value=response) as api:
            url,identifier=paypal.create(order())
            self.assertEqual(identifier,PID);self.assertIn('sandbox.paypal.com',url)
            payload=api.call_args.args[2]
            self.assertEqual(payload['purchase_units'][0]['amount'],{'value':'20.00','currency_code':'USD'})
            self.assertEqual(payload['purchase_units'][0]['custom_id'],OID)
            context=payload['payment_source']['paypal']['experience_context']
            self.assertEqual(context['shipping_preference'],'NO_SHIPPING')
            self.assertIn('/paypal-return.html?key=',context['return_url'])
            self.assertNotIn('phone',json.dumps(payload));self.assertNotIn('email',json.dumps(payload))
        for url in ('https://evil.example/checkoutnow?token='+PID,
                    'https://www.sandbox.paypal.com.evil.example/checkoutnow?token='+PID,
                    'http://www.sandbox.paypal.com/checkoutnow?token='+PID,
                    'https://www.sandbox.paypal.com/checkoutnow?token=OTHERORDER0000001'):
            with patch.object(paypal,'api',return_value={'id':PID,'links':[{'rel':'approve','href':url}]}):
                with self.assertRaises(paypal.PaymentMismatch):paypal.create(order())

    def test_completed_capture_is_reconciled_against_amount_merchant_and_reference(self):
        with patch.object(paypal,'api',side_effect=[remote(),capture_data()]):
            self.assertEqual(paypal.reconcile(order()),{'transaction':CID,'kind':'paid','amount':20})
        for field,value in (('custom_id','TABO-0000000000000003'),('invoice_id','bad'),
                            ('reference_id','bad'),('amount',{'currency_code':'USD','value':'1'}),
                            ('payee',{'merchant_id':'OTHER123456'})):
            bad=remote();bad['purchase_units'][0][field]=value
            with patch.object(paypal,'api',return_value=bad):
                with self.assertRaises(paypal.PaymentMismatch):paypal.reconcile(order())
        bad=capture_data();bad['amount']['currency_code']='EGP'
        with patch.object(paypal,'api',side_effect=[remote(),bad]):
            with self.assertRaises(paypal.PaymentMismatch):paypal.reconcile(order())
        with patch.object(paypal,'api',side_effect=[remote(),capture_data('PENDING')]):
            self.assertIsNone(paypal.reconcile(order()))
        with patch.object(paypal,'api',side_effect=[remote(),capture_data('PARTIALLY_REFUNDED')]):
            self.assertEqual(paypal.reconcile(order())['kind'],'review_notice')

    def test_capture_only_after_paypal_approval_and_uses_stable_idempotency(self):
        approved=remote('APPROVED');approved['purchase_units'][0].pop('payments')
        with patch.object(paypal,'api',side_effect=[approved,{},remote(),capture_data()]) as api:
            self.assertEqual(paypal.capture(order())['kind'],'paid')
            self.assertEqual(api.call_args_list[1].args,('/v2/checkout/orders/'+PID+'/capture','POST',{},'capture:'+OID))
        pending=copy.deepcopy(approved);pending['status']='CREATED'
        with patch.object(paypal,'api',return_value=pending) as api:
            self.assertIsNone(paypal.capture(order()));self.assertEqual(api.call_count,1)
        with patch.object(paypal,'api',side_effect=[remote(),remote(),capture_data()]) as api:
            self.assertEqual(paypal.capture(order())['kind'],'paid')
            self.assertFalse(any(call.args[1:2]==('POST',) for call in api.call_args_list))
        with patch.object(paypal,'_access_token',return_value='synthetic_access_token'),patch.object(paypal,'_transport',return_value={}) as transport:
            paypal.api('/v2/checkout/orders','POST',{},'create:'+OID);first=transport.call_args.args[3]['PayPal-Request-Id']
            paypal.api('/v2/checkout/orders','POST',{},'create:'+OID)
            self.assertEqual(transport.call_args.args[3]['PayPal-Request-Id'],first);self.assertEqual(len(first),36)

    def test_webhook_verification_uses_paypal_service_not_arbitrary_certificate_fetches(self):
        headers=Message()
        for name,value in {'PAYPAL-AUTH-ALGO':'SHA256withRSA','PAYPAL-CERT-URL':'https://api.sandbox.paypal.com/v1/notifications/certs/TEST',
                           'PAYPAL-TRANSMISSION-ID':'abc-123','PAYPAL-TRANSMISSION-SIG':'synthetic-signature','PAYPAL-TRANSMISSION-TIME':'2026-10-28T00:00:00Z'}.items():headers[name]=value
        event={'event_type':'PAYMENT.CAPTURE.COMPLETED','resource':{'id':CID}}
        with patch.object(paypal,'api',return_value={'verification_status':'SUCCESS'}) as api:
            self.assertTrue(paypal.verify_webhook(event,headers))
            self.assertEqual(api.call_args.args[0],'/v1/notifications/verify-webhook-signature')
        headers.replace_header('PAYPAL-CERT-URL','https://private.example/cert')
        with patch.object(paypal,'api') as api:
            self.assertFalse(paypal.verify_webhook(event,headers));api.assert_not_called()
        self.assertEqual(paypal.webhook_capture_id(event),CID)
        self.assertIsNone(paypal.webhook_capture_id({'event_type':'CHECKOUT.ORDER.APPROVED','resource':{'id':PID}}))

    def test_token_cache_is_bounded_and_invalidated_on_credentials_change(self):
        with patch.object(paypal,'_TOKEN',None),patch.object(paypal,'_transport',return_value={'access_token':'synthetic_access_token_123','token_type':'Bearer','expires_in':3600}) as transport:
            self.assertEqual(paypal._access_token(),'synthetic_access_token_123')
            paypal._access_token();self.assertEqual(transport.call_count,1)
            with patch.dict(os.environ,{'PAYPAL_SANDBOX_CLIENT_SECRET':'different_synthetic_secret_123'}):
                paypal._access_token();self.assertEqual(transport.call_count,2)
            self.assertEqual(transport.call_args.args[0],'/v1/oauth2/token')

    def test_transport_bounds_responses_rejects_redirects_and_sanitizes_errors(self):
        class Response(io.BytesIO):pass
        opener=Mock();opener.open.return_value=Response(b'{}')
        with patch.object(paypal,'build_opener',return_value=opener):
            self.assertEqual(paypal._transport('/v2/checkout/orders','POST',{},{}),{})
            request=opener.open.call_args.args[0]
            self.assertEqual(request.full_url,'https://api-m.sandbox.paypal.com/v2/checkout/orders')
        self.assertIsNone(paypal.NoRedirect().redirect_request(None,None,302,'',{},'https://evil.example'))
        opener.open.side_effect=HTTPError('https://synthetic.invalid',500,'PRIVATE-BUYER',{},io.BytesIO(b'SECRET'))
        with patch.object(paypal,'build_opener',return_value=opener):
            with self.assertRaises(paypal.PayPalUnavailable) as caught:paypal._transport('/v2/checkout/orders','GET',None,{})
        self.assertNotIn('PRIVATE',str(caught.exception));self.assertNotIn('SECRET',str(caught.exception))


class PayPalHTTPTests(HTTPCase):
    def setUp(self):
        super().setUp()
        p=patch.dict(os.environ,ENV);p.start();self.addCleanup(p.stop)

    def test_missing_configuration_never_changes_default_checkout_or_exposes_paypal(self):
        self.assertFalse(app.checkout_ready());self.assertFalse(app.paypal_ready('USD'))
        self.assertEqual(self.post('/api/orders',{'paymentProvider':'paypal'})[0],503)
        with patch.dict(os.environ,{'TABO_PAYPAL_ENABLED':'0'}):self.assertFalse(paypal.available('USD'))

    def test_server_prices_paypal_sigma_pair_in_usd_without_kashier_conversion(self):
        saved=[]
        with patch.object(app,'paypal_ready',side_effect=lambda c:c=='USD'),patch.object(app.Handler,'pricing_country',return_value='KW'),\
             patch.object(app.store,'insert',side_effect=lambda value:saved.append(value.copy())),\
             patch.object(app.store,'patch',return_value=order()),\
             patch.object(paypal,'create',return_value=('https://www.sandbox.paypal.com/checkoutnow?token='+PID,PID)),\
             patch.object(app.checkout_pricing,'issue') as quote:
            body={'paymentProvider':'paypal','country':'KW','items':[{'id':'book20'},{'id':'book21'}],
                  'customer':{'name':'Synthetic buyer','email':'fixture@example.invalid','phone':'01000000000'},
                  'total':1,'currency':'EGP'}
            status,result,_=self.post('/api/orders',body)
        self.assertEqual(status,201);self.assertEqual(saved[0]['total'],20)
        self.assertEqual(saved[0]['currency'],'USD');self.assertEqual(saved[0]['paymentMethod'],'PayPal')
        self.assertEqual(saved[0]['paymentMode'],'test');self.assertIsNone(saved[0]['pricingQuote']);quote.assert_not_called()

    def test_capture_requires_original_cookie_exact_provider_order_and_verified_api(self):
        value=order();self.install(value)
        body={'key':'2'*64,'token':PID};cookie={'Cookie':app.checkout_cookie_name(OID)+'=synthetic-browser-secret'}
        with patch.object(paypal,'capture',return_value={'transaction':CID,'kind':'paid','amount':20}) as capture,\
             patch.object(app.store,'apply_paypal_event',return_value={'ok':True,'status':'PAID'}) as journal:
            self.assertEqual(self.post('/api/paypal/capture',body)[0],403)
            self.assertEqual(self.post('/api/paypal/capture',{**body,'token':'OTHERORDER0000001'},cookie)[0],400)
            self.assertEqual(self.post('/api/paypal/capture',body,{**cookie,'Sec-Fetch-Site':'cross-site'})[0],403)
            capture.assert_not_called();journal.assert_not_called()
            status,result,_=self.post('/api/paypal/capture',body,cookie)
            self.assertEqual((status,result['paid']),(200,True));journal.assert_called_once()
        self.assertEqual(self.db.usage,{})

    def test_pending_capture_and_unsigned_webhook_never_activate(self):
        value=order();self.install(value)
        body={'key':'2'*64,'token':PID};cookie={'Cookie':app.checkout_cookie_name(OID)+'=synthetic-browser-secret'}
        with patch.object(paypal,'capture',return_value=None),patch.object(app.store,'apply_paypal_event') as journal:
            self.assertEqual(self.post('/api/paypal/capture',body,cookie)[1]['paid'],False);journal.assert_not_called()
        with patch.object(paypal,'verify_webhook',return_value=False),patch.object(app.store,'apply_paypal_event') as journal:
            self.assertEqual(self.post('/api/paypal/webhook',{'event_type':'PAYMENT.CAPTURE.COMPLETED','resource':{'id':CID}})[0],401)
            journal.assert_not_called()

    def test_signed_webhook_is_reconciled_and_refund_lag_is_not_acknowledged(self):
        value=order();self.install(value)
        event={'event_type':'PAYMENT.CAPTURE.COMPLETED','resource':{'id':CID}}
        with patch.object(paypal,'verify_webhook',return_value=True),patch.object(paypal,'api',return_value=capture_data()),\
             patch.object(paypal,'reconcile',return_value={'transaction':CID,'kind':'paid','amount':20}),\
             patch.object(app.store,'apply_paypal_event',return_value={'ok':True,'status':'PAID'}) as journal:
            self.assertEqual(self.post('/api/paypal/webhook',event)[1]['paid'],True);journal.assert_called_once()
        event={'event_type':'PAYMENT.CAPTURE.REFUNDED','resource':{'supplementary_data':{'related_ids':{'capture_id':CID}}}}
        with patch.object(paypal,'verify_webhook',return_value=True),patch.object(paypal,'api',return_value=capture_data()),\
             patch.object(paypal,'reconcile',return_value={'transaction':CID,'kind':'paid','amount':20}),\
             patch.object(app.store,'apply_paypal_event') as journal:
            self.assertEqual(self.post('/api/paypal/webhook',event)[0],503);journal.assert_not_called()
