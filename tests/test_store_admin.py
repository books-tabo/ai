"""Synthetic catalog/recovery boundaries. No production mail or source files."""
import base64
import concurrent.futures
import copy
import io
import json
import os
import time
import unittest
from urllib.request import Request
from urllib.error import HTTPError
from unittest.mock import patch

import app
import catalog
import checkout_recovery as recovery
import localization
import seo
import store_catalog as editor
from support import HTTPCase, order
from test_owner_dashboard import ENV as OWNER_ENV

AUTH={'id':'a'*64,'owner':'b'*64,'device':'c'*64,'session':'d'*64}
ENV={**OWNER_ENV,'TABO_STORE_ADMIN_ENABLED':'1','TABO_RECOVERY_ENABLED':'1'}

class CatalogEditTests(unittest.TestCase):
 def setUp(self):
  env=patch.dict(os.environ,ENV,clear=True);env.start();self.addCleanup(env.stop);editor.invalidate();self.addCleanup(editor.invalidate)
 def test_rejects_policy_download_and_price_attacks(self):
  for value in ({'unknown':1},{'books':{'book20':{'previewEnabled':True}}},{'books':{'book1':{'pdf':'/secret'}}},{'books':{'book1':{'image':'https://evil.invalid/a.jpg'}}},{'independentPrices':{'book20':{'EGP':1,'USD':1}}},{'order':['book1']},{'independentPrices':{'book1':{'EGP':0,'USD':float('nan')}}}):
   with self.assertRaises((ValueError,TypeError)):editor.validate(value)
  offers=copy.deepcopy(catalog.OFFERS);offers['sigma-couple']['prices']['USD']=1
  with self.assertRaises(ValueError):editor.validate({'offers':offers})
  offers=copy.deepcopy(catalog.OFFERS);offers['change-starter']['books']=['book1','book20']
  with self.assertRaises(ValueError):editor.validate({'offers':offers})
 def test_one_snapshot_changes_public_metadata_order_and_both_price_engines(self):
  doc=editor.validate({'books':{'book1':{'name':'<script>synthetic</script>','short':'Edited summary'}},'order':list(reversed(catalog.BOOKS)), 'independentPrices':{'book1':{'EGP':250,'USD':12}}})
  with editor.context(doc):
   self.assertEqual(list(seo.current_books())[0],'book21')
   config=localization.pricing_for_country(catalog.PRICES,'EG')
   self.assertEqual(config['bookPolicies']['book1'],{'price':250,'bundleEligible':False,'previewEnabled':True})
   self.assertEqual(catalog.total_for_items(['book1','book2'],config),449)
   self.assertEqual(catalog.total_for_items(['book20','book21'],localization.pricing_for_country(catalog.PRICES,'SA')),20)
   html=seo.render((app.ROOT/'index.html').read_text(),'/','tabobooks.com','index.html')
   self.assertIn('const BOOKS = [{"id":"book21"',html)
   self.assertNotIn('<script>synthetic</script>',html)
   self.assertIn('&lt;script&gt;synthetic&lt;/script&gt;',html)
  self.assertEqual(seo.current_books()['book1']['name'],seo.BOOKS['book1']['name'])
 def test_thread_snapshots_do_not_leak_drafts_between_requests(self):
  def work(n):
   with editor.context({'books':{'book1':{'name':str(n)}}}):return seo.current_books()['book1']['name']
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as p:self.assertEqual(list(p.map(work,range(30))),[str(i) for i in range(30)])
  self.assertEqual(seo.current_books()['book1']['name'],seo.BOOKS['book1']['name'])
 def test_cached_document_and_revision_are_one_snapshot(self):
  with patch.object(editor,'enabled',return_value=True),patch.object(editor.store,'request',return_value=[{'published':{'books':{'book1':{'name':'Version seven'}}},'revision':7,'reminders_enabled':True}]):
   with editor.context():self.assertEqual(editor.revision(),7);self.assertEqual(seo.current_books()['book1']['name'],'Version seven')
  self.assertEqual(editor.revision(),0)
 def test_public_refresh_snapshot_is_bounded_and_never_used_for_strict_reads(self):
  document={'books':{'book1':{'name':'Recently published'}}}
  with patch.object(editor,'enabled',return_value=True),patch.object(editor.time,'monotonic',return_value=100):
   editor._CACHE=(94,document,True,7)
   editor._LOCK.acquire()
   try:
    self.assertEqual(editor.published(with_revision=True,public_read=True),(document,7))
    with self.assertRaises(editor.store.StoreUnavailable):editor.published(with_revision=True)
    editor._CACHE=(69,document,True,7)
    with self.assertRaises(editor.store.StoreUnavailable):editor.published(public_read=True)
    editor._CACHE=None
    with self.assertRaises(editor.store.StoreUnavailable):editor.published(public_read=True)
   finally:editor._LOCK.release()
 def test_conflicts_and_auth_are_not_silently_overwritten(self):
  with patch.object(editor,'rpc',return_value={'conflict':True}):
   with self.assertRaises(FileExistsError):editor.save(AUTH,{'document':{},'draftRevision':2})
  with patch.object(editor,'rpc',return_value=None):
   with self.assertRaises(PermissionError):editor.read(AUTH)
 def test_draft_covers_not_public_and_missing_covers_not_published(self):
  with self.assertRaises(ValueError):editor.validate({'books':{'book1':{'imageWidth':'invalid'}}})
  with editor.context({}),patch.object(editor.store,'request') as request:
   self.assertIsNone(editor.cover('f'*64));request.assert_not_called()
  with patch.object(editor.store,'request',return_value=[]):
   with self.assertRaises(ValueError):editor.verify_covers({'books':{'book1':{'image':'/catalog-covers/'+'f'*64+'.webp','imageWidth':100,'imageHeight':200}}})
 def test_cover_decode_is_sandboxed_and_only_sanitized_derivative_is_saved(self):
  from PIL import Image
  output=io.BytesIO();Image.new('RGB',(20,30)).save(output,format='WEBP');payload=output.getvalue()
  with patch.object(editor.media_sandbox,'run',return_value=(payload,{'ok':True})) as worker,patch.object(editor.store,'request') as request:
   image=editor.upload({'imageData':base64.b64encode(b'synthetic-original').decode()})
   self.assertEqual((image['imageWidth'],image['imageHeight']),(20,30));worker.assert_called_once()
   self.assertEqual(base64.b64decode(request.call_args.args[2]['data_base64']),payload)
   self.assertNotIn('synthetic-original',str(request.call_args))

class ReminderTests(unittest.TestCase):
 def setUp(self):
  env=patch.dict(os.environ,ENV,clear=True);env.start();self.addCleanup(env.stop)
 def test_links_restore_no_order_or_download_credentials(self):
  claim={'order_id':'TABO-0000000000000001','email':'synthetic@example.invalid','language':'ar','attempts':1}
  value=recovery.message(claim)
  self.assertEqual(value['to'],['synthetic@example.invalid']);self.assertIn('/resume-checkout?token=',value['text'])
  for secret in ('statusKey','pending.html?key=','/api/download','checkout_session','synthetic@example.invalid'):
   self.assertNotIn(secret,value['html'])
  self.assertNotEqual(recovery.token(claim['order_id']),recovery.token(claim['order_id'],'unsubscribe'))
  self.assertNotIn(claim['order_id'],value['text'])
 def test_rechecks_after_claim_and_never_sends_if_payment_or_optout_wins(self):
  claim={'order_id':'TABO-0000000000000001','email':'synthetic@example.invalid','language':'ar','attempts':1}
  with patch.object(recovery,'enabled',return_value=True),patch.object(editor,'rpc',side_effect=[claim,False,True]) as rpc,patch.object(recovery.order_receipts,'send') as send:
   self.assertTrue(recovery.process_one());send.assert_not_called();self.assertEqual(rpc.call_args.kwargs['state'],'skipped')
 def test_transport_retry_is_leased_and_uses_stable_idempotency(self):
  claim={'order_id':'TABO-0000000000000001','email':'synthetic@example.invalid','language':'ar','attempts':1}
  with patch.object(recovery,'enabled',return_value=True),patch.object(editor,'rpc',side_effect=[claim,True,True]) as rpc,patch.object(recovery.order_receipts,'send',side_effect=recovery.order_receipts.DeliveryError(True)) as send:
   self.assertTrue(recovery.process_one());self.assertEqual(rpc.call_args.kwargs['state'],'retry');self.assertEqual(send.call_args.args[1],'checkout-recovery-'+claim['order_id'])
 def test_disabled_feature_never_enrolls_or_sends(self):
  with patch.dict(os.environ,{'TABO_RECOVERY_ENABLED':'0'}),patch.object(editor,'rpc') as rpc:
   self.assertFalse(recovery.enroll('TABO-0000000000000001'));self.assertFalse(recovery.process_one());rpc.assert_not_called()

class AdminHTTPTests(HTTPCase):
 def setUp(self):
  super().setUp();env=patch.dict(os.environ,ENV);env.start();self.addCleanup(env.stop);self.install(order())
 def get(self,path):
  try:r=self.opener.open(Request(self.base+path,headers={'User-Agent':'SyntheticBrowser/1'}),timeout=5)
  except HTTPError as e:r=e
  with r:return r.status,r.read(),r.headers
 def test_anonymous_and_cross_origin_cannot_read_or_write_catalog(self):
  with patch.object(app.owner_dashboard,'authenticated',return_value=None),patch.object(editor,'rpc') as rpc:
   self.assertEqual(self.get('/api/admin-dashboard/catalog')[0],403)
   for path in ('catalog/draft','catalog/publish','catalog/cover','recovery'):
    self.assertEqual(self.post('/api/admin-dashboard/'+path,{'document':{},'draftRevision':0})[0],403)
   rpc.assert_not_called()
  self.assertEqual(self.post('/api/admin-dashboard/catalog/draft',{}, {'Origin':'https://evil.invalid'})[0],403)
 def test_stale_price_revision_cannot_start_payment(self):
  with patch.object(editor,'published',return_value=({},0)),patch.object(app.store,'insert') as insert,patch.object(app,'payment_checkout') as pay:
   code,data,_=self.post('/api/orders',{'catalogRevision':99})
   self.assertEqual(code,409);self.assertEqual(data['code'],'CATALOG_REFRESH_REQUIRED');insert.assert_not_called();pay.assert_not_called()
 def test_refreshing_catalog_keeps_public_html_available_without_relaxing_checkout(self):
  self.addCleanup(editor.invalidate)
  editor._CACHE=(time.monotonic()-6,{'books':{'book4':{'name':'Published book title'}}},True,7)
  with patch.object(editor,'enabled',return_value=True):
   editor._LOCK.acquire()
   try:
    for route in ('/books/book4','/categories/business','/offers/change-starter'):
     code,body,_=self.get(route)
     self.assertEqual(code,200,route);self.assertIn(b'Published book title',body)
    self.assertEqual(self.get('/api/store-config?country=SA')[0],503)
    self.assertEqual(self.get('/checkout')[0],503)
   finally:editor._LOCK.release()
 def test_recovery_get_is_safe_and_unsubscribe_requires_post(self):
  with patch.object(recovery,'enabled',return_value=True),patch.object(recovery,'unsubscribe') as stop:
   code,body,headers=self.get('/stop-reminders?token='+'a'*64)
   self.assertEqual(code,200);self.assertIn(b'checkout-recovery.js',body);self.assertIn('noindex',headers['X-Robots-Tag']);stop.assert_not_called()
   self.assertEqual(self.post('/api/checkout-reminders/unsubscribe',{'token':'a'*64})[0],200);stop.assert_called_once()
 def test_paid_cart_link_exposes_no_download_or_buyer(self):
  with patch.object(recovery,'enabled',return_value=True),patch.object(recovery,'cart',return_value={'completed':True}),patch.object(editor,'published',return_value=({},0)):
   code,body,_=self.get('/resume-checkout?token='+'a'*64)
   self.assertEqual(code,200);self.assertIn(b'"completed":true',body)
   for value in (b'statusKey',b'checkout_session',b'/api/download',b'TaboTracking'):self.assertNotIn(value,body)
