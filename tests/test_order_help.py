"""Synthetic assistance: all DB/provider calls stubbed; never emails real customers."""
import copy
import json
import os
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from urllib.request import Request
from urllib.error import HTTPError
import app
import order_help as help
from support import HTTPCase,order
from test_order_receipts import MAIL_ENV,paid

HELP_ENV={**MAIL_ENV,'TABO_ORDER_HELP_ENABLED':'1','TABO_ORDER_HELP_SCHEMA_READY':'1',
          'TABO_SESSION_BINDING_KEY':'synthetic-session-binding-key-32-bytes'}

class HelpTests(unittest.TestCase):
    def setUp(self):
        p=patch.dict(os.environ,HELP_ENV,clear=True);p.start();self.addCleanup(p.stop)

    def test_nonexistent_and_valid_requests_have_same_shape_no_order_lookup(self):
        with patch.object(help.store,'find') as find,patch.object(help.store,'help_request',return_value=False) as request:
            first=help.request({'order_id':'TABO-0000000000000001','email':' Reader@example.invalid '},'127.0.0.1','d'*64)
            args=request.call_args.args
            self.assertEqual(args[2],'reader@example.invalid');self.assertNotIn('reader@example.invalid',args[3:])
            request.return_value=True
            second=help.request({'order_id':'TABO-0000000000000002','email':'reader@example.invalid'},'127.0.0.1','d'*64)
            self.assertRegex(first,r'^[a-f0-9]{64}$');self.assertRegex(second,r'^[a-f0-9]{64}$')
            self.assertNotEqual(first,second);find.assert_not_called()
            request.reset_mock()
            help.request({'order_id':'bad','email':'bad'},'x','d'*64);request.assert_not_called()

    def test_code_and_session_hashes_purpose_separated_and_no_token_in_database(self):
        challenge='a'*64
        entered=help.code(challenge)
        with patch.object(help.store,'help_verify',return_value=True) as verify:
            cookie=help.verify({'challenge':challenge,'code':entered.translate(str.maketrans('0123456789','٠١٢٣٤٥٦٧٨٩'))},'b'*64)
            self.assertRegex(cookie,r'^[a-f0-9]{64}\.[a-f0-9]{64}$')
            args=verify.call_args.args;self.assertEqual(args[1],help.fingerprint('verify',challenge+'|'+entered))
            token=cookie.split('.')[1];self.assertNotIn(token,args)
            self.assertEqual(help.identity(cookie,'b'*64),(challenge,help.fingerprint('session',token),'b'*64))
            self.assertIsNone(help.identity(cookie,None));self.assertIsNone(help.identity('bad','b'*64))
            help.verify({'challenge':challenge,'code':'bad'},'b'*64)
            self.assertEqual(verify.call_args.args[1],help.fingerprint('verify',challenge+'|invalid'))

    def test_worker_persisted_address_only_idempotent_retry_refund_and_expiry(self):
        value=paid();original=copy.deepcopy(value)
        claim={'challenge_id':'a'*64,'kind':'otp','order_id':value['id'],'email_hash':help.fingerprint('email',value['buyer']['email']),
               'created_at':datetime.now(timezone.utc).isoformat(),'language':'ar','attempts':1}
        with patch.object(help.store,'ready',return_value=True),patch.object(help.store,'help_claim',return_value=claim),patch.object(help.store,'find',return_value=value),patch.object(help.store,'help_finish') as finish,patch.object(help.order_receipts,'send',return_value='synthetic-message') as send:
            self.assertTrue(help.process_one());payload=send.call_args.args[0]
            self.assertEqual(payload['to'],[value['buyer']['email']]);self.assertIn(help.code('a'*64),payload['text'])
            self.assertNotIn(value['statusKey'],payload['text']);self.assertNotIn(value['id'],payload['text'])
            first_key=send.call_args.args[1];help.process_one();self.assertEqual(send.call_args.args[1],first_key)
            claim['kind']='receipt';help.process_one();self.assertNotEqual(send.call_args.args[1],first_key)
            self.assertIn('/pending.html?key=',send.call_args.args[0]['text']);self.assertEqual(value,original)
            send.reset_mock();value['status']='REFUNDED';help.process_one();send.assert_not_called();self.assertEqual(finish.call_args.args[3],'skipped')
            value['status']='PAID';send.side_effect=help.order_receipts.DeliveryError(True);help.process_one();self.assertEqual(finish.call_args.args[3],'retry')
            claim['attempts']=5;help.process_one();self.assertEqual(finish.call_args.args[3],'failed')
            send.reset_mock();claim['kind']='otp';claim['created_at']=(datetime.now(timezone.utc)-timedelta(minutes=11)).isoformat()
            help.process_one();send.assert_not_called();self.assertEqual(finish.call_args.args[-1],'expired')
            claim['email_hash']='c'*64;help.process_one();send.assert_not_called()
        with patch.dict(os.environ,{'TABO_ORDER_HELP_ENABLED':'0'}),patch.object(help.store,'help_claim') as claim:
            self.assertFalse(help.process_one());claim.assert_not_called()

class HelpHTTPTests(HTTPCase):
    def setUp(self):
        super().setUp();p=patch.dict(os.environ,HELP_ENV);p.start();self.addCleanup(p.stop);self.install(order())
    def get(self,path,headers=None):
        try:r=self.opener.open(Request(self.base+path,headers={'User-Agent':'SyntheticBrowser/1',**(headers or {})}))
        except HTTPError as e:r=e
        with r:return r.status,r.read(),r.headers
    def test_private_page_no_tracking_noindex_and_unauthorized_api(self):
        code,body,headers=self.get('/order-help')
        self.assertEqual(code,200);self.assertIn('noindex',headers['X-Robots-Tag']);self.assertEqual(headers['Cache-Control'],'no-store')
        for code in (b'google-ads-tracking',b'meta-tracking',b'snap-tracking',b'ga4-tracking'):self.assertNotIn(code,body)
        self.assertEqual(self.get('/api/order-help/status')[0],403)
        self.assertEqual(self.post('/api/order-help/request',{}, {'Origin':'https://other.invalid'})[0],403)
        with patch.dict(os.environ,{'TABO_ORDER_HELP_ENABLED':'0'}):self.assertEqual(self.post('/api/order-help/request',{})[0],503)
    def test_http_verification_cookie_status_allowlist_and_download_stays_protected(self):
        value=paid();self.install(value)
        with patch.object(help.store,'help_request',return_value=False):
            code,data,headers=self.post('/api/order-help/request',{'order_id':value['id'],'email':value['buyer']['email']})
            self.assertEqual(code,202);self.assertEqual(set(data),{'ok','challenge'});self.assertNotIn('Set-Cookie',headers)
        cookie='a'*64+'.'+'b'*64
        with patch.object(help,'verify',return_value=cookie):
            code,data,headers=self.post('/api/order-help/verify',{'challenge':'a'*64,'code':'123456'})
            self.assertEqual(code,200);self.assertNotIn(cookie,json.dumps(data))
            for flag in ('HttpOnly','Secure','SameSite=Strict','Path=/','Max-Age=900'):self.assertIn(flag,headers['Set-Cookie'])
        with patch.object(help.store,'help_order',return_value=value['id']):
            code,body,headers=self.get('/api/order-help/status',{'Cookie':help.COOKIE+'='+cookie})
            data=json.loads(body);self.assertEqual(code,200);self.assertEqual(data['order']['status'],'PAID')
            for secret in (value['buyer']['email'],value['buyer']['phone'],'transactionId','buyer'):self.assertNotIn(secret,body.decode())
            self.assertEqual(self.get('/api/downloads?order='+value['id'],{'Cookie':help.COOKIE+'='+cookie})[0],403)
            self.assertEqual(self.post('/api/download-session',{'key':value['statusKey']},{'Cookie':help.COOKIE+'='+cookie})[0],403)
            self.assertEqual(self.db.usage,{})
        with patch.object(help.store,'help_resend',return_value={'accepted':True,'state':'pending'}):
            code,data,_=self.post('/api/order-help/resend',{}, {'Cookie':help.COOKIE+'='+cookie});self.assertEqual(code,200);self.assertTrue(data['accepted'])
