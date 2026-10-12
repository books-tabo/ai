import json
import os
import unittest
from unittest.mock import patch

import app


class FakeResponse:
    def __init__(self, payload):
        self.payload=json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _limit):
        return self.payload


class KashierTests(unittest.TestCase):
    def test_official_webhook_signature_vector(self):
        data={
            'amount':1,
            'channel':'online | e-commerce',
            'currency':'EGP',
            'kashierOrderId':'9ad06b17-755b-4e21-9774-aff3e2726ac9',
            'merchantOrderId':'1653481557813',
            'method':'card',
            'orderReference':'TEST-ORD-38855',
            'status':'SUCCESS',
            'transactionId':'TX-249893963',
            'transactionResponseCode':'00',
            'signatureKeys':['amount','channel','currency','kashierOrderId',
                             'merchantOrderId','method','orderReference','status',
                             'transactionId','transactionResponseCode'],
        }
        with patch.dict(os.environ,{'KASHIER_PAYMENT_API_KEY':'11111'}):
            self.assertTrue(app.verify_kashier(
                data,'9610477b2255b2a8ef84fd89adfaa5f1305ff9c20324205851890f1ea03109f4'))
            self.assertFalse(app.verify_kashier(data,'0'*64))

    def test_webhook_rejects_undeclared_or_complex_values(self):
        with patch.dict(os.environ,{'KASHIER_PAYMENT_API_KEY':'test-key'}):
            self.assertFalse(app.verify_kashier(
                {'signatureKeys':['missing']},'0'*64))
            self.assertFalse(app.verify_kashier(
                {'nested':{'x':1},'signatureKeys':['nested']},'0'*64))
            self.assertFalse(app.verify_kashier(
                {'amount':1,'signatureKeys':['amount','amount']},'0'*64))

    def test_payment_match_requires_provider_amount_currency_and_success(self):
        order={'paymentMethod':'Kashier','total':199,'currency':'EGP'}
        data={'status':'SUCCESS','transactionResponseCode':'00','amount':'199.00','currency':'EGP'}
        self.assertTrue(app.kashier_payment_matches(order,data))
        self.assertFalse(app.kashier_payment_matches(order,{**data,'amount':'198.99'}))
        self.assertFalse(app.kashier_payment_matches(order,{**data,'currency':'USD'}))
        self.assertFalse(app.kashier_payment_matches(order,{**data,'status':'PENDING'}))

    def test_live_session_uses_server_credentials_and_safe_destination(self):
        environment={
            'KASHIER_MODE':'live',
            'KASHIER_MERCHANT_ID':'MID-51370-903',
            'KASHIER_PAYMENT_API_KEY':'payment-key-at-least-16',
            'KASHIER_SECRET_KEY':'secret-key-at-least-16',
            'KASHIER_CURRENCIES':'EGP',
            'TABO_PUBLIC_URL':'https://tabobooks.com',
        }
        reply={'_id':'67adc07584f10c00121f6739',
               'sessionUrl':'https://payments.kashier.io/session/67adc07584f10c00121f6739?mode=live'}
        captured=[]

        def fake_urlopen(request,timeout):
            captured.append((request,timeout))
            return FakeResponse(reply)

        order={'id':'TABO-0123456789ABCDEF','total':199,'currency':'EGP','statusKey':'a'*64}
        customer={'email':'buyer@example.com'}
        with patch.dict(os.environ,environment,clear=False),patch.object(app,'urlopen',fake_urlopen):
            url,session_id=app.kashier_checkout(order,customer)
        self.assertEqual(url,reply['sessionUrl'])
        self.assertEqual(session_id,reply['_id'])
        request,timeout=captured[0]
        self.assertEqual(request.full_url,'https://api.kashier.io/v3/payment/sessions')
        self.assertEqual(timeout,15)
        payload=json.loads(request.data)
        self.assertEqual(payload['amount'],'199.00')
        self.assertEqual(payload['serverWebhook'],'https://tabobooks.com/api/kashier/webhook')
        self.assertEqual(payload['allowedMethods'],'card,wallet')
        self.assertEqual(payload['paymentType'],'credit')
        self.assertEqual(payload['redirectMethod'],'get')
        self.assertEqual(payload['customer']['reference'],'TABO-0123456789ABCDEF')
        self.assertNotIn('KASHIER_SECRET_KEY',json.dumps(payload))

    def test_session_rejects_untrusted_redirect_host(self):
        environment={
            'KASHIER_MODE':'live',
            'KASHIER_MERCHANT_ID':'MID-51370-903',
            'KASHIER_PAYMENT_API_KEY':'payment-key-at-least-16',
            'KASHIER_SECRET_KEY':'secret-key-at-least-16',
            'KASHIER_CURRENCIES':'EGP',
            'TABO_PUBLIC_URL':'https://tabobooks.com',
        }
        reply={'_id':'67adc07584f10c00121f6739',
               'sessionUrl':'https://attacker.example/session/67adc07584f10c00121f6739'}
        with patch.dict(os.environ,environment,clear=False),patch.object(
                app,'urlopen',lambda *_args,**_kwargs:FakeResponse(reply)):
            with self.assertRaisesRegex(ValueError,'destination'):
                app.kashier_checkout(
                    {'id':'TABO-0123456789ABCDEF','total':199,'currency':'EGP','statusKey':'a'*64},
                    {'email':'buyer@example.com'})


if __name__=='__main__':
    unittest.main()
