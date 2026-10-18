import concurrent.futures
from unittest.mock import patch
import app
from support import HTTPCase,order,kashier_payload,paymob_payload

class WebhookHTTPTests(HTTPCase):
    def test_kashier_storage_failure_is_503_then_retry_commits_once(self):
        o=order();self.install(o);self.db.fail_next=True
        body,headers=kashier_payload(o)
        with patch.object(app,'reconcile_kashier',return_value='paid'):
            self.assertEqual(self.post('/api/kashier/webhook',body,headers)[0],503)
            self.assertEqual(self.db.orders[o['id']]['status'],'PENDING');self.assertEqual(self.db.events,{})
            self.assertEqual(self.post('/api/kashier/webhook',body,headers)[0],200)
            self.assertEqual(self.post('/api/kashier/webhook',body,headers)[0],200)
        self.assertEqual(len(self.db.events),1);self.assertEqual(self.db.orders[o['id']]['status'],'PAID')

    def test_kashier_invalid_signature_and_tampering_never_grant(self):
        o=order();self.install(o);body,headers=kashier_payload(o)
        with patch.object(app,'reconcile_kashier') as lookup:
            self.assertEqual(self.post('/api/kashier/webhook',body,{'x-kashier-signature':'0'*64})[0],401)
            for field,value in [('amount',200),('currency','USD'),('merchantOrderId','TABO-0000000000000002')]:
                b,h=kashier_payload(o);b['data'][field]=value
                self.assertEqual(self.post('/api/kashier/webhook',b,h)[0],401)
            # Correct HMAC over an incomplete declared key set must not authenticate.
            body['data']['signatureKeys']=['status']
            self.assertEqual(self.post('/api/kashier/webhook',body,headers)[0],401)
            lookup.assert_not_called()
        self.assertEqual(self.db.events,{})

    def test_kashier_concurrent_retries_and_unsigned_event_cannot_change_operation(self):
        o=order();self.install(o);body,headers=kashier_payload(o);body['event']='refund'
        with patch.object(app,'reconcile_kashier',return_value='paid'):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                codes=list(pool.map(lambda _:self.post('/api/kashier/webhook',body,headers)[0],range(8)))
        self.assertEqual(codes,[200]*8);self.assertEqual(len(self.db.events),1)
        self.assertEqual(self.db.orders[o['id']]['status'],'PAID')

    def test_kashier_reconciliation_unavailable_is_not_acknowledged(self):
        o=order();self.install(o);b,h=kashier_payload(o)
        with patch.object(app,'reconcile_kashier',side_effect=app.store.StoreUnavailable(503)):
            self.assertEqual(self.post('/api/kashier/webhook',b,h)[0],503)
        self.assertEqual(self.db.events,{})

    def test_each_confirmed_revocation_is_terminal_in_any_order(self):
        for n,kind in enumerate(('refund','void','reversal','partial_refund'),1):
            o=order(number=n);self.install(o);body,h=kashier_payload(o)
            with patch.object(app,'reconcile_kashier',return_value=kind):
                self.assertEqual(self.post('/api/kashier/webhook',body,h)[0],200)
                self.assertEqual(self.post('/api/kashier/webhook',body,h)[0],200)
            with patch.object(app,'reconcile_kashier',return_value='paid'):
                self.assertFalse(self.post('/api/kashier/webhook',body,h)[1]['paid'])
            self.assertNotEqual(self.db.orders[o['id']]['status'],'PAID')

    def test_paymob_transient_then_retry_and_duplicate(self):
        o=order('Paymob');self.install(o);self.db.fail_next=True;path,b=paymob_payload(o)
        self.assertEqual(self.post(path,b)[0],503)
        self.assertEqual(self.db.orders[o['id']]['status'],'PENDING')
        self.assertEqual(self.post(path,b)[0],200);self.assertEqual(self.post(path,b)[0],200)
        self.assertEqual(len(self.db.events),1)

    def test_paymob_signed_order_binding_tampering_and_revocation(self):
        o=order('Paymob');self.install(o)
        path,b=paymob_payload(o)
        for field,value in [('amount_cents',1),('currency','USD'),('integration_id',999)]:
            p,body=paymob_payload(o,**{field:value})
            self.assertEqual(self.post(p,body)[0],400)
        b['obj']['order']['merchant_order_id']='TABO-0000000000000002'
        self.assertEqual(self.post(path,b)[0],400) # unsigned reference cannot redirect a signed order id
        p,body=paymob_payload(o);self.assertEqual(self.post(p,body)[0],200)
        p,body=paymob_payload(o,is_refunded=True);self.assertEqual(self.post(p,body)[0],200)
        p,body=paymob_payload(o);self.assertFalse(self.post(p,body)[1]['paid'])
        self.assertEqual(self.db.orders[o['id']]['status'],'REFUNDED')

    def test_paymob_invalid_signature_concurrency_and_void(self):
        o=order('Paymob');self.install(o);path,b=paymob_payload(o)
        self.assertEqual(self.post('/api/paymob/webhook?hmac='+'0'*128,b)[0],401)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(list(pool.map(lambda _:self.post(path,b)[0],range(6))),[200]*6)
        self.assertEqual(len(self.db.events),1)
        p,b=paymob_payload(o,is_voided=True);self.assertFalse(self.post(p,b)[1]['paid'])

    def test_checkout_remains_disabled(self):
        self.install(order())
        self.assertEqual(self.post('/api/orders',{'items':[{'id':'book1'}]})[0],503)
