import os
import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app
import supabase_store


class SecureDownloadTests(unittest.TestCase):
    def make_handler(self, order, raw_session='browser-session-secret', user_agent='TABO-Test/1'):
        handler=object.__new__(app.Handler)
        handler.headers=Message()
        handler.headers['User-Agent']=user_agent
        handler.headers['Cookie']=(
            f'{app.download_cookie_name(order["id"])}={raw_session}'
        )
        return handler

    def make_order(self, raw_session='browser-session-secret', user_agent='TABO-Test/1'):
        secret='server-secret-for-tests'
        device=app.hmac.new(secret.encode(),user_agent.encode(),app.hashlib.sha256).hexdigest()
        return {
            'id':'TABO-0123456789ABCDEF','status':'PAID',
            'items':[{'id':'book13','name':'بنت تخطت الثلاثين'}],
            'buyer':{'name':'تابو نور','phone':'01122965693'},
            'downloadSessionHash':app.hash_secret(raw_session),
            'downloadDeviceHash':device,
            'downloadSessionExpiresAt':(
                datetime.now(timezone.utc)+timedelta(days=1)
            ).isoformat(),
        }

    def test_download_session_is_cookie_and_device_bound(self):
        order=self.make_order()
        handler=self.make_handler(order)
        with patch.dict(os.environ,{'SUPABASE_SECRET_KEY':'server-secret-for-tests'}), \
             patch.object(app.store,'ready',return_value=True), \
             patch.object(app.store,'find',return_value=order):
            self.assertEqual(handler.authorized_download_order(order['id']),order)
            handler.headers.replace_header('User-Agent','Different-Browser/9')
            self.assertIsNone(handler.authorized_download_order(order['id']))

    def test_expired_session_is_rejected(self):
        order=self.make_order()
        order['downloadSessionExpiresAt']=(
            datetime.now(timezone.utc)-timedelta(seconds=1)
        ).isoformat()
        handler=self.make_handler(order)
        with patch.dict(os.environ,{'SUPABASE_SECRET_KEY':'server-secret-for-tests'}), \
             patch.object(app.store,'ready',return_value=True), \
             patch.object(app.store,'find',return_value=order):
            self.assertIsNone(handler.authorized_download_order(order['id']))

    def test_cookie_has_browser_security_attributes(self):
        cookie=app.secure_cookie('__Host-tabo_download_0123456789ABCDEF','secret',3600)
        self.assertIn('HttpOnly',cookie)
        self.assertIn('Secure',cookie)
        self.assertIn('SameSite=Strict',cookie)
        self.assertIn('Path=/',cookie)
        self.assertNotIn('Domain=',cookie)

    def test_store_claim_uses_atomic_rpc(self):
        expected={'allowed':True,'remaining':1,'used':1}
        with patch.object(supabase_store,'request',return_value=expected) as request:
            result=supabase_store.claim_download(
                'TABO-0123456789ABCDEF','book13','a'*64,'b'*64,222
            )
        self.assertEqual(result,expected)
        request.assert_called_once_with('/rest/v1/rpc/claim_order_download','POST',{
            'p_order_id':'TABO-0123456789ABCDEF','p_book_id':'book13',
            'p_session_hash':'a'*64,'p_device_hash':'b'*64,'p_page_count':222
        })

    def test_names_are_compared_after_safe_whitespace_normalization(self):
        self.assertEqual(app.normalized_name('  تابو   نور '),app.normalized_name('تابو نور'))

    def run_server(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def test_shared_success_url_without_cookie_is_denied(self):
        order=self.make_order()
        base=self.run_server()
        with patch.dict(os.environ,{'SUPABASE_SECRET_KEY':'server-secret-for-tests'}), \
             patch.object(app.store,'ready',return_value=True), \
             patch.object(app.store,'find',return_value=order):
            request=Request(base+'/api/downloads?order='+order['id'],
                            headers={'User-Agent':'TABO-Test/1'})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request,timeout=3)
        self.assertEqual(caught.exception.code,403)

    def test_manifest_reports_durable_remaining_count(self):
        order=self.make_order()
        base=self.run_server()
        cookie=f'{app.download_cookie_name(order["id"])}=browser-session-secret'
        with patch.dict(os.environ,{'SUPABASE_SECRET_KEY':'server-secret-for-tests'}), \
             patch.object(app.store,'ready',return_value=True), \
             patch.object(app.store,'find',return_value=order), \
             patch.object(app.store,'download_usage',return_value={'book13':1}):
            request=Request(base+'/api/downloads?order='+order['id'],headers={
                'User-Agent':'TABO-Test/1','Cookie':cookie
            })
            with urlopen(request,timeout=3) as response:
                payload=json.loads(response.read())
        self.assertEqual(payload['books'][0]['remaining'],1)
        self.assertEqual(payload['downloadLimit'],2)

    def test_third_download_is_blocked_before_private_file_read(self):
        order=self.make_order()
        base=self.run_server()
        cookie=f'{app.download_cookie_name(order["id"])}=browser-session-secret'
        body=json.dumps({'order_id':order['id']}).encode()
        with patch.dict(os.environ,{'SUPABASE_SECRET_KEY':'server-secret-for-tests'}), \
             patch.object(app.store,'ready',return_value=True), \
             patch.object(app.store,'find',return_value=order), \
             patch.object(app.store,'download_usage',return_value={'book13':2}), \
             patch.object(app.store,'original') as original:
            request=Request(base+'/api/download/book13',data=body,method='POST',headers={
                'User-Agent':'TABO-Test/1','Cookie':cookie,
                'Content-Type':'application/json'
            })
            with self.assertRaises(HTTPError) as caught:
                urlopen(request,timeout=3)
        self.assertEqual(caught.exception.code,403)
        original.assert_not_called()


if __name__=='__main__':
    unittest.main()
