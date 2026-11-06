"""Independent synthetic receipts: no live email, payment or customer data."""
import copy
import json
import os
import io
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import app
import checkout_fields
import order_receipts as receipts
from support import HTTPCase, order

MAIL_ENV = {'TABO_RECEIPTS_ENABLED':'1','TABO_RECEIPTS_SCHEMA_READY':'1',
            'TABO_RECEIPT_SENDER_VERIFIED':'1','TABO_RECEIPT_FROM':'orders@tabobooks.com',
            'RESEND_API_KEY':'re_synthetic_not_a_real_secret'}


def paid():
    value = order()
    value.update(status='PAID', paymentMode='live', total=560.59, currency='EGP',
                 pricingQuote={'displayTotal':'9.99','displayCurrency':'USD'})
    value['buyer'].update(email='reader@example.invalid',language='ar',receiptRequested=True)
    return value


class CheckoutFieldsTests(unittest.TestCase):
    def test_arabic_persian_and_formatted_phones_preserve_the_same_identity(self):
        for raw in ('٠٠٩٦٦ (٥٠) ١٢٣-٤٥٦٧','۰۰۹۶۶۵۰۱۲۳۴۵۶۷','+966 50 123 4567'):
            value, errors = checkout_fields.customer({'name':' قارئ اختباري ', 'email':' reader@example.invalid ', 'phone':raw})
            self.assertEqual(errors,{})
            self.assertEqual(value,{'name':'قارئ اختباري','email':'reader@example.invalid','phone':'+966501234567'})
        self.assertEqual(checkout_fields.phone('٠١٠٠٠٠٠٠٠٠٠'),'01000000000')

    def test_invalid_contacts_return_only_field_names(self):
        _, errors = checkout_fields.customer({'name':'م','email':'bad\r\n@x.invalid','phone':'call-me'})
        self.assertEqual(set(errors),{'name','email','phone'})
        self.assertEqual(set(checkout_fields.customer(None)[1]),{'name','email','phone'})
        self.assertIn('phone',checkout_fields.customer({'phone':' '*100+'12345678'})[1])


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,MAIL_ENV,clear=True);env.start();self.addCleanup(env.stop)

    def test_disabled_review_unverified_or_other_domain_cannot_send(self):
        self.assertTrue(receipts.enabled())
        for change in ({'TABO_RECEIPTS_ENABLED':'0'},{'TABO_SITE_ENV':'review'},
                       {'TABO_RECEIPT_SENDER_VERIFIED':'0'},{'TABO_RECEIPT_FROM':'someone@another.invalid'}):
            with patch.dict(os.environ,change),patch.object(receipts.store,'claim_receipt') as claim:
                self.assertFalse(receipts.process_one());claim.assert_not_called()

    def test_provider_diagnostics_are_fixed_and_do_not_expose_private_details(self):
        cases = [
            (401, 'validation_error', 'API key is invalid re_private', 'api_key'),
            (403, 'validation_error', 'The secret.invalid domain is not verified. Please, add and verify your domain.', 'sender_unverified'),
            (403, 'validation_error', 'You can only send testing emails to private@example.invalid', 'test_recipient'),
            (429, 'daily_quota_exceeded', 'private quota', 'quota'),
            (429, 'rate_limit_exceeded', 'private rate', 'rate_limit'),
            (422, 'validation_error', 'private@example.invalid rejected', 'provider_rejected'),
        ]
        for status, name, message, reason in cases:
            error = HTTPError(receipts.SEND_URL,status,'private detail',{},io.BytesIO(json.dumps({'name':name,'message':message}).encode()))
            with patch.object(receipts,'OPEN',side_effect=error):
                with self.assertRaises(receipts.DeliveryError) as caught:receipts.send({},'synthetic')
            self.assertEqual(caught.exception.status,status)
            self.assertEqual(caught.exception.reason,reason)
            self.assertNotIn('private',str(caught.exception.__dict__))

    def test_bilingual_receipt_uses_paid_amount_safe_links_and_escaped_names(self):
        value=paid();value['buyer']['name']='<Synthetic & Reader>'
        for language in ('ar','en'):
            value['buyer']['language']=language
            payload=receipts.message(value)
            self.assertEqual(payload['to'],['reader@example.invalid'])
            self.assertIn('560.59 EGP',payload['text']);self.assertIn('9.99 USD',payload['text'])
            self.assertIn('/pending.html?key='+value['statusKey'],payload['text'])
            self.assertIn('/order-help',payload['text'])
            self.assertIn('&lt;Synthetic &amp; Reader&gt;',payload['html'])
            self.assertNotIn('attachments',payload);self.assertNotIn('/api/download',payload['html'])
            self.assertNotIn('reader@example.invalid',payload['html'])
        for status in ('PENDING','REFUNDED','REVIEW','REVERSED'):
            value['status']=status
            with self.assertRaises(ValueError):receipts.message(value)

    def test_worker_rechecks_paid_state_and_commits_delivery_with_claim_lease(self):
        value=paid();claim={'order_id':value['id'],'created_at':datetime.now(timezone.utc).isoformat(),'attempts':1}
        with patch.object(receipts.store,'ready',return_value=True),patch.object(receipts.store,'claim_receipt',return_value=claim),patch.object(receipts.store,'find',return_value=value),patch.object(receipts.store,'finish_receipt') as finish,patch.object(receipts,'send',return_value='synthetic-message') as send:
            self.assertTrue(receipts.process_one());send.assert_called_once()
            args=finish.call_args.args;self.assertEqual(args[0],value['id']);self.assertEqual(args[2:4],('sent','synthetic-message'))
            value['status']='REFUNDED';send.reset_mock();receipts.process_one();send.assert_not_called();self.assertEqual(finish.call_args.args[2],'skipped')
            value['status']='PAID';claim['created_at']=(datetime.now(timezone.utc)-timedelta(hours=21)).isoformat()
            receipts.process_one();send.assert_not_called();self.assertEqual(finish.call_args.args[-1],'expired')

    def test_transport_uses_stable_idempotency_key_and_never_follows_redirects(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'{"id":"synthetic-message"}'
        with patch.object(receipts,'OPEN',return_value=Response()) as transport:
            for _ in range(2):self.assertEqual(receipts.send(receipts.message(paid()),paid()['id']),'synthetic-message')
            requests=[call.args[0] for call in transport.call_args_list]
            self.assertEqual(requests[0].get_header('Idempotency-key'),requests[1].get_header('Idempotency-key'))
            self.assertEqual(requests[0].full_url,'https://api.resend.com/emails')
            self.assertEqual(requests[0].get_header('User-agent'),'TABO-Books/1.0 (+https://tabobooks.com)')
            self.assertEqual(json.loads(requests[0].data)['to'],['reader@example.invalid'])
            self.assertIsNone(receipts.NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid'))
        for status, retryable in ((302,False),(422,False),(429,True),(503,True)):
            with patch.object(receipts,'OPEN',side_effect=HTTPError(receipts.SEND_URL,status,'synthetic',{},None)):
                with self.assertRaises(receipts.DeliveryError) as caught:receipts.send({},paid()['id'])
                self.assertEqual(caught.exception.retryable,retryable)
                self.assertNotIn('example.invalid',str(caught.exception))
        with patch.object(receipts,'OPEN',side_effect=URLError('synthetic private provider detail')):
            with self.assertRaises(receipts.DeliveryError) as caught:receipts.send({},paid()['id'])
            self.assertNotIn('private provider',str(caught.exception))


class CheckoutContactHTTPTests(HTTPCase):
    def setUp(self):
        super().setUp()
        country=patch.object(app.Handler,'pricing_country',return_value='EG');country.start();self.addCleanup(country.stop)
        for target in ('checkout_ready','paypal_ready'):
            p=patch.object(app,target,return_value=True);p.start();self.addCleanup(p.stop)

    def test_normalized_contacts_persist_privately_but_never_enter_public_response(self):
        captured=[]
        with patch.object(app.store,'insert',side_effect=lambda value:captured.append(copy.deepcopy(value))),patch.object(app.store,'patch',return_value={'id':'synthetic'}),patch.object(app,'kashier_checkout',return_value=('https://payments.kashier.io/synthetic','synthetic-session')):
            status,payload,_=self.post('/api/orders',{'items':[{'id':'book1'}],'country':'EG',
                'customer':{'name':' قارئ اختباري ','email':' reader@example.invalid ','phone':'٠١٠ ٠٠٠٠ ٠٠٠٠'}})
        self.assertEqual(status,201)
        self.assertEqual(captured[0]['buyer']['phone'],'01000000000')
        self.assertEqual(captured[0]['buyer']['email'],'reader@example.invalid')
        self.assertFalse(captured[0]['buyer']['receiptRequested'])
        self.assertNotIn('reader@example.invalid',json.dumps(payload));self.assertNotIn('buyer',payload['order'])

    def test_invalid_fields_do_not_create_an_order_or_call_gateway(self):
        with patch.object(app.store,'insert') as insert,patch.object(app,'kashier_checkout') as gateway:
            status,payload,_=self.post('/api/orders',{'items':[{'id':'book1'}],'country':'EG',
                'customer':{'name':'x','email':'bad','phone':'no'}})
        self.assertEqual(status,400);self.assertEqual(payload['code'],'INVALID_CUSTOMER')
        self.assertEqual(set(payload['fields']),{'name','email','phone'})
        insert.assert_not_called();gateway.assert_not_called()

    def test_emailed_return_link_still_requires_original_browser_and_spends_no_download(self):
        value=paid();secret='synthetic-original-browser-secret'
        value['checkoutSessionHash']=app.hash_secret(secret);self.install(value)
        status,_,_=self.post('/api/download-session',{'key':value['statusKey']})
        self.assertEqual(status,403)
        cookie=app.checkout_cookie_name(value['id'])+'='+secret
        status,result,_=self.post('/api/download-session',{'key':value['statusKey']},{'Cookie':cookie})
        self.assertEqual(status,200);self.assertEqual(result['success_url'],'/success.html?order='+value['id'])
        self.assertEqual(self.db.usage,{})
        status,_,_=self.post('/api/download-session',{'key':value['statusKey']})
        self.assertEqual(status,403)
