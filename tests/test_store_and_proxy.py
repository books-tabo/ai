import io,json,os,unittest
from email.message import Message
from urllib.error import HTTPError,URLError
from unittest.mock import patch
import app,proxy_identity,supabase_store as store,localization

class StoreAndProxyTests(unittest.TestCase):
    def test_public_book_metadata_never_reads_or_exposes_download_counts(self):
        row={'book_id':'book22','rating_sum':10,'review_count':2,
             'download_count':987654,'page_count':419,'file_size_bytes':12185488}
        with patch.object(store,'request',return_value=[row]) as request:
            result=store.book_stats()
        self.assertEqual(result['book22'],{'rating':5.0,'reviews':2,
            'pageCount':419,'fileSizeBytes':12185488})
        self.assertNotIn('download_count',request.call_args.kwargs['params']['select'])

    def test_error_classification_is_sanitized(self):
        with patch.dict(os.environ,{'SUPABASE_URL':'https://synthetic.invalid','SUPABASE_SECRET_KEY':'sb_secret_test'}):
            for status,code,expected in [(503,'XX000',store.StoreUnavailable),(409,'23505',store.UniqueConflict),(409,'23514',store.StoreError)]:
                error=HTTPError('https://synthetic.invalid',status,'synthetic',{},io.BytesIO(json.dumps({'code':code,'message':'PRIVATE BUYER'}).encode()))
                with patch.object(store,'urlopen',side_effect=error):
                    with self.assertRaises(expected) as caught:store.request('/rest/v1/orders')
                self.assertEqual(caught.exception.status,status);self.assertEqual(caught.exception.code,code)
                self.assertNotIn('PRIVATE',str(caught.exception))
            with patch.object(store,'urlopen',side_effect=URLError('private host')):
                with self.assertRaises(store.StoreUnavailable):store.request('/rest/v1/orders')

    def test_key_headers_distinguish_opaque_keys_from_legacy_jwt(self):
        self.assertEqual(store.auth_headers('sb_secret_synthetic'),{'apikey':'sb_secret_synthetic'})
        self.assertEqual(store.auth_headers('eyJsynthetic.jwt'),{'apikey':'eyJsynthetic.jwt','Authorization':'Bearer eyJsynthetic.jwt'})

    def test_original_uses_correct_headers_for_both_key_types(self):
        class Response(io.BytesIO):
            headers={}
        for key in ('sb_secret_synthetic','eyJsynthetic.jwt'):
            captured=[]
            def read(request,timeout):
                captured.append(request);return Response(b'%PDF-synthetic')
            with patch.dict(os.environ,{'SUPABASE_URL':'https://synthetic.invalid','SUPABASE_SECRET_KEY':key}),patch.object(store,'urlopen',read):
                self.assertEqual(store.original('book1.pdf'),b'%PDF-synthetic')
            request=captured[0]
            self.assertIn('/object/authenticated/tabo-originals/book1.pdf',request.full_url)
            self.assertEqual(request.get_header('Apikey'),key)
            self.assertEqual(request.get_header('Authorization'),'Bearer '+key if key.startswith('eyJ') else None)

    def test_direct_connection_ignores_all_forged_headers(self):
        h=Message();h['CF-Connecting-IP']='41.32.1.1';h['CF-IPCountry']='EG';h['CF-Ray']='forged';h['True-Client-IP']='41.32.1.1';h['X-Forwarded-For']='41.32.1.1'
        with patch.dict(os.environ,{'TABO_TRUSTED_PROXY_CIDRS':''}):
            self.assertEqual(proxy_identity.client_ip('8.8.8.8',h),'8.8.8.8')
        self.assertIsNone(localization.country_from_proxy_headers(h))

    def test_trusted_chain_is_walked_from_right_not_spoofed_leftmost(self):
        h=Message();h['X-Forwarded-For']='41.32.1.1, 8.8.8.8, 10.0.0.2'
        with patch.dict(os.environ,{'TABO_TRUSTED_PROXY_CIDRS':'10.0.0.0/24'}):
            self.assertEqual(proxy_identity.client_ip('10.0.0.1',h),'8.8.8.8')
            h.replace_header('X-Forwarded-For','invalid,10.0.0.2')
            self.assertEqual(proxy_identity.client_ip('10.0.0.1',h),'10.0.0.1')
            h['X-Forwarded-For']='41.32.1.1'
            self.assertEqual(proxy_identity.client_ip('10.0.0.1',h),'10.0.0.1')

    def test_untrusted_proxy_cannot_assign_egypt_pricing_or_limiter_identity(self):
        h=object.__new__(app.Handler);h.headers=Message();h.client_address=('8.8.8.8',1)
        h.headers['CF-IPCountry']='EG';h.headers['CF-Ray']='forged';h.headers['CF-Connecting-IP']='41.32.1.1'
        with patch.dict(os.environ,{'TABO_TRUSTED_PROXY_CIDRS':''}):
            self.assertEqual(h.client_id(),'8.8.8.8');self.assertNotEqual(h.pricing_country('EG'),'EG')

    def test_currency_capability_and_production_modes_fail_closed(self):
        with patch.dict(os.environ,{'KASHIER_CURRENCIES':'EGP,USD','KASHIER_VERIFIED_CURRENCIES':'','TABO_PAYMENT_PROVIDER':'kashier'},clear=True):
            self.assertFalse(app.payment_currency_supported('EGP'));self.assertFalse(app.payment_currency_supported('USD'))
            os.environ['KASHIER_VERIFIED_CURRENCIES']='EGP,USD'
            self.assertTrue(app.payment_currency_supported('EGP'));self.assertTrue(app.payment_currency_supported('USD'))
            self.assertNotIn('USD',app.kashier_currencies()) # Display conversion is not direct USD acceptance.
        with patch.dict(os.environ,{'PAYMOB_MODE':'live','PAYMOB_TEST_EGP_METHOD_ID':'71'},clear=True):
            self.assertFalse(app.paymob_mode());self.assertEqual(localization.payment_methods(),{})
        with patch.dict(os.environ,{'KASHIER_MODE':'live','KASHIER_PAYMENT_API_KEY':'test-key'},clear=True):
            self.assertEqual(app.kashier_key('PAYMENT_API_KEY'),'')
