import concurrent.futures,hashlib,hmac,os
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
import app
from support import HTTPCase,order,ENV

def future(seconds=3600):return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()

class DownloadHTTPTests(HTTPCase):
    def legacy(self,**changes):
        o=order();o.update(status='PAID',transactionId='TX-SYNTHETIC',token='a'*64,downloadTokenExpiresAt=future())
        o.update(changes);self.install(o);return o
    def activation(self,**changes):
        return {'token':'a'*64,'name':'مشتري اختباري','phone':'01000000000',**changes}

    def test_arabic_activation_normalizes_whitespace_and_is_single_use(self):
        self.legacy();code,data,headers=self.post('/api/download-session',self.activation(name='  مشتري   اختباري  ',phone=' 01000000000 '))
        self.assertEqual(code,200);self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertNotIn('name',data);self.assertNotIn('phone',data)
        self.assertEqual(self.post('/api/download-session',self.activation())[0],403)

    def test_invalid_name_phone_expiry_and_missing_buyer_are_rejected(self):
        for change in [{'name':'شخص آخر'},{'phone':'01000000001'}]:
            self.legacy();code,body,_=self.post('/api/download-session',self.activation(**change))
            self.assertEqual(code,403);self.assertNotIn('01000000000',str(body))
        for changes in [{'downloadTokenExpiresAt':future(-5)},{'downloadTokenExpiresAt':None},{'buyer':None},{'buyer':{}},{'buyer':{'name':'','phone':''}}]:
            self.legacy(**changes);self.assertEqual(self.post('/api/download-session',self.activation())[0],403)

    def test_concurrent_legacy_claim_has_one_winner(self):
        self.legacy()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            codes=list(pool.map(lambda _:self.post('/api/download-session',self.activation())[0],range(2)))
        self.assertEqual(sorted(codes),[200,403]) # HTTP regression only; real SQL race is a separate integration test.

    def paid(self,number):
        o=order(number=number);o.update(status='PAID',transactionId=f'TX-PAID-{number}',
          items=[{'id':book_id,'name':'Synthetic'} for book_id in app.BOOKS],
          downloadSessionHash=app.hash_secret('synthetic-session'),downloadSessionExpiresAt=future(),
          downloadDeviceHash=hmac.new(ENV['TABO_SESSION_BINDING_KEY'].encode(),b'SyntheticBrowser/1',hashlib.sha256).hexdigest())
        return o

    def test_two_buyers_one_ip_can_download_full_catalog_and_no_third(self):
        orders=[self.paid(1),self.paid(2)];self.install(*orders)
        with patch.object(app.store,'original',return_value=b'%PDF-synthetic') as original,patch.object(app,'watermarked',return_value=(b'%PDF-synthetic',12)):
            for o in orders:
                headers={'Cookie':app.download_cookie_name(o['id'])+'=synthetic-session'}
                for attempt in (1,2,3):
                    for book_id in app.BOOKS:
                        code,_,_=self.post(f'/api/download/{book_id}',{'order_id':o['id']},headers)
                        self.assertEqual(code,200 if attempt<=2 else 403,(o['id'],attempt,book_id,code))
            self.assertEqual(original.call_count,4*len(app.BOOKS))

    def test_session_does_not_depend_on_supabase_key_rotation_but_ua_change_is_denied(self):
        o=self.paid(1);self.install(o)
        h={'Cookie':app.download_cookie_name(o['id'])+'=synthetic-session'}
        with patch.dict(os.environ,{'SUPABASE_SECRET_KEY':'sb_secret_rotated'}),patch.object(app.store,'original',return_value=b'x'),patch.object(app,'watermarked',return_value=(b'%PDF-test',12)):
            self.assertEqual(self.post('/api/download/book1',{'order_id':o['id']},h)[0],200)
            self.assertEqual(self.post('/api/download/book1',{'order_id':o['id']},{**h,'User-Agent':'Different/1'})[0],403)

    def test_revoked_order_denies_future_download_without_reading_original(self):
        o=self.paid(1);o['status']='REFUNDED';self.install(o)
        with patch.object(app.store,'original') as read:
            self.assertEqual(self.post('/api/download/book1',{'order_id':o['id']},{'Cookie':app.download_cookie_name(o['id'])+'=synthetic-session'})[0],403)
            read.assert_not_called()
