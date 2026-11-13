"""GA4 paid-only payload and public bootstrap, using synthetic fixtures."""
import json,os,unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
import journey_tracking as ga4
from support import HTTPCase,order
GA4_ENV={'TABO_GA4_ID':'G-SYNTHETIC1','TABO_GA4_PURCHASE_SINCE':'2026-10-29T00:00:00Z'}
class GA4Tests(unittest.TestCase):
 def test_actual_currency_opaque_stable_transaction_and_no_backfill(self):
  now=datetime(2026,10,29,1,tzinfo=timezone.utc);value=order()
  value.update(status='PAID',paymentMode='live',transactionId='synthetic-payment',paidAt=now.isoformat(),total=560.60)
  with patch.dict(os.environ,GA4_ENV):
   payload=ga4.purchase(value,now);self.assertEqual(payload['value'],560.60);self.assertEqual(payload['currency'],'EGP');self.assertEqual(payload['items'],[{'item_id':'book1','quantity':1}])
   self.assertEqual(payload,ga4.purchase(value,now));self.assertNotIn(value['id'],json.dumps(payload));self.assertNotIn('buyer',json.dumps(payload))
   for changes in ({'status':'PENDING'},{'status':'REFUNDED'},{'paymentMode':'test'},{'transactionId':None},{'paidAt':'2026-10-28T23:59:59Z'},{'total':'NaN'},{'items':[{'id':'book999'}]},{'currency':'XXX'}):
    self.assertIsNone(ga4.purchase({**value,**changes},now))
   self.assertIsNone(ga4.purchase(value,now+timedelta(hours=25)))
   with patch.dict(os.environ,{'TABO_GA4_PURCHASE_SINCE':''}):self.assertIsNone(ga4.purchase(value,now))
  with patch.dict(os.environ,{'TABO_GA4_ID':''}):self.assertIsNone(ga4.purchase(value,now))
class GA4HTTPTests(HTTPCase):
 def test_public_nonce_catalog_csp_and_private_review_exclusion(self):
  def get(path):
   try:r=self.opener.open(Request(self.base+path,headers={'User-Agent':'SyntheticBrowser/1'}))
   except HTTPError as e:r=e
   with r:return r.read(),r.headers
  with patch.dict(os.environ,GA4_ENV):
   for path in ('/','/books/book20','/checkout','/contact','/success.html'):
    body,headers=get(path);self.assertIn(b'data-ga4-id="G-SYNTHETIC1"',body);self.assertIn('https://www.google-analytics.com',headers['Content-Security-Policy']);self.assertIn(b'data-ga4-catalog=',body)
   for path in ('/order-help','/pending.html','/paypal-return.html'):
    body,headers=get(path);self.assertNotIn(b'/ga4-tracking.js',body);self.assertIn('noindex',headers['X-Robots-Tag'])
   with patch.dict(os.environ,{'TABO_SITE_ENV':'review'}):self.assertNotIn(b'/ga4-tracking.js',get('/')[0])
