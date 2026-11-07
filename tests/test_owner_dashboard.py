"""Synthetic owner-only dashboard tests. No production mail, order or PDF access."""
import json,os,unittest
from http.cookies import SimpleCookie
from unittest.mock import patch
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError
import app
import owner_dashboard as dashboard
from support import HTTPCase,order
from test_order_receipts import MAIL_ENV

ENV={**MAIL_ENV,'TABO_DASHBOARD_ENABLED':'1','TABO_DASHBOARD_SCHEMA_READY':'1',
'TABO_DASHBOARD_ADMIN_EMAIL':'owner@example.invalid','TABO_SESSION_BINDING_KEY':'synthetic-session-binding-key-32-bytes'}
COOKIE='a'*64+'.'+'b'*64
class DashboardTests(unittest.TestCase):
 def setUp(self):
  p=patch.dict(os.environ,ENV,clear=True);p.start();self.addCleanup(p.stop)
 def test_fixed_owner_generic_challenge_and_private_code(self):
  with patch.object(dashboard,'rpc',return_value=True) as rpc,patch.object(dashboard.order_receipts,'send') as send:
   cid=dashboard.login_request({'email':' OWNER@example.invalid '},'127.0.0.1','d'*64)
   self.assertRegex(cid,r'^[a-f0-9]{64}$');self.assertEqual(send.call_args.args[0]['to'],['owner@example.invalid'])
   code=send.call_args.args[0]['text'].split(': ')[1].split('\n')[0]
   self.assertRegex(code,r'^[0-9]{6}$');self.assertEqual(rpc.call_args.kwargs['code'],dashboard.digest('code',cid+'|'+code))
   send.reset_mock();rpc.reset_mock()
   other=dashboard.login_request({'email':'someone@example.invalid'},'x','d'*64)
   self.assertRegex(other,r'^[a-f0-9]{64}$');rpc.assert_not_called();send.assert_not_called()
 def test_sessions_are_purpose_bound_and_logout_revokes_only_dashboard(self):
  with patch.object(dashboard,'rpc',return_value=True) as rpc:
   cookie=dashboard.login_verify({'challenge':'a'*64,'code':'١٢٣٤٥٦'},'d'*64)
   self.assertRegex(cookie,r'^[a-f0-9]{64}\.[a-f0-9]{64}$');self.assertEqual(rpc.call_args.kwargs['code'],dashboard.digest('code','a'*64+'|123456'))
   self.assertNotIn(cookie.split('.')[1],rpc.call_args.kwargs.values())
   self.assertTrue(dashboard.authenticated(cookie,'d'*64));dashboard.logout(cookie,'d'*64);self.assertEqual(rpc.call_args.args[0],'owner_session_logout')
   self.assertIsNone(dashboard.authenticated('bad','d'*64));self.assertIsNone(dashboard.credentials(cookie,None))
 def test_report_filters_timezone_and_no_public_data(self):
  with patch.object(dashboard,'rpc',return_value={'countries':[{'country':'SA','orders':1}]}) as rpc:
   self.assertIsNone(dashboard.report(None,'d'*64,{}));rpc.assert_not_called()
   result=dashboard.report(COOKIE,'d'*64,{'from':['2026-10-01'],'to':['2026-10-29'],'country':['SA']})
   self.assertEqual(rpc.call_args.kwargs['from'],'2026-10-01T00:00:00+03:00');self.assertEqual(rpc.call_args.kwargs['to'],'2026-10-30T00:00:00+03:00')
   self.assertEqual(result['countrySales'],[{'country':'SA','orders':1}]);self.assertEqual(result['countries']['SA'],'السعودية')
   self.assertNotIn('pdf',json.dumps(result['catalog']))
   for params in ({'country':['ZZ']},{'status':['INVALID']},{'offset':['10001']},{'query':['a@b.invalid']},{'from':['2020-01-01'],'to':['2026-10-29']}):
    with self.assertRaises(ValueError):dashboard.report(COOKIE,'d'*64,params)
 def test_source_is_fixed_categories_no_url_values_or_pii(self):
  self.assertEqual(dashboard.source({'googleClick':True,'source':'private@example.invalid'}),('google','paid'))
  self.assertEqual(dashboard.source({'metaClick':True}),('meta','unknown'))
  self.assertEqual(dashboard.source({'source':'facebook','medium':'paid_social'}),('meta','paid'))
  self.assertEqual(dashboard.source({'referrer':'https://www.google.com/search?q=private'}),('google','organic'))
  self.assertEqual(dashboard.source({'referrer':'https://other.invalid/private@example.invalid?token=secret'}),('other','referral'))
  with patch.object(dashboard,'rpc',return_value={'resumed':False,'ttl':1800}) as rpc:
   token,ttl=dashboard.insight_start({'path':'/books/book20','googleClick':True,'email':'private@example.invalid'},None,'d'*64,'192.0.2.1','SA')
   self.assertEqual(ttl,1800);self.assertNotIn(token,rpc.call_args.kwargs.values());self.assertNotIn('192.0.2.1',rpc.call_args.kwargs.values());self.assertNotIn('private@example.invalid',rpc.call_args.kwargs.values())
   for path in ('/success.html','/order-help','/admin','/books/book999','/?email=private'):
    with self.assertRaises(ValueError):dashboard.insight_start({'path':path},None,'d'*64,'x','SA')
   rpc.reset_mock();self.assertFalse(dashboard.insight_event({'event':'Purchase'},'a'*64,'d'*64));rpc.assert_not_called()
 def test_disabled_dashboard_cannot_link_orders(self):
  with patch.dict(os.environ,{'TABO_DASHBOARD_ENABLED':'0'}),patch.object(dashboard,'rpc') as rpc:
   dashboard.link_order('a'*64,'b'*64,'TABO-0000000000000001');rpc.assert_not_called()
 def test_mail_failure_is_diagnosable_without_exposing_codes_or_addresses(self):
  with patch.object(dashboard,'rpc',return_value=True),patch.object(dashboard.order_receipts,'send',side_effect=dashboard.order_receipts.DeliveryError(False,401,'api_key')),patch('builtins.print') as log:
   cid=dashboard.login_request({'email':'owner@example.invalid'},'client','d'*64)
   self.assertRegex(cid,r'^[a-f0-9]{64}$')
   self.assertEqual(log.call_args.args,('Owner login mail: failed status=401 reason=api_key',))
   self.assertNotIn(cid,str(log.call_args))

class DashboardHTTPTests(HTTPCase):
 def setUp(self):
  super().setUp();p=patch.dict(os.environ,ENV);p.start();self.addCleanup(p.stop);self.install(order())
 def get(self,path,cookie=None):
  headers={'User-Agent':'SyntheticBrowser/1'}
  if cookie:headers['Cookie']=cookie
  try:r=self.opener.open(Request(self.base+path,headers=headers))
  except HTTPError as e:r=e
  with r:return r.status,r.read(),r.headers
 def test_anonymous_and_forged_sessions_cannot_read_financials(self):
  with patch.object(dashboard,'rpc',return_value=None) as rpc:
   self.assertEqual(self.get('/api/admin-dashboard/report')[0],403);rpc.assert_not_called()
   self.assertEqual(self.get('/api/admin-dashboard/report',dashboard.COOKIE+'='+COOKIE)[0],403)
   self.assertEqual(self.get('/api/admin-dashboard/session')[0],403)
  self.assertEqual(self.post('/api/admin-dashboard/verify',{}, {'Origin':'https://evil.invalid'})[0],403)
 def test_private_ui_has_no_trackers_owner_email_or_sensitive_bootstrap(self):
  code,body,headers=self.get('/admin');self.assertEqual(code,200);self.assertIn('noindex',headers['X-Robots-Tag']);self.assertEqual(headers['Cache-Control'],'no-store')
  for item in (b'ga4-tracking.js',b'meta-tracking.js',b'store-insights.js',b'google-ads-tracking.js',b'owner@example.invalid',b'SUPABASE_SECRET_KEY'):self.assertNotIn(item,body)
  self.assertIn(b'admin-dashboard.js?v=',body)
 def test_cookie_is_secure_and_does_not_authorize_payment_or_download(self):
  with patch.object(dashboard,'login_verify',return_value=COOKIE):
   code,data,headers=self.post('/api/admin-dashboard/verify',{'code':'123456','challenge':'a'*64});self.assertEqual(code,200)
   for part in ('Secure','HttpOnly','SameSite=Strict','Max-Age=28800','Path=/'):self.assertIn(part,headers['Set-Cookie'])
   self.assertNotIn(COOKIE,json.dumps(data))
  cookie={'Cookie':dashboard.COOKIE+'='+COOKIE}
  self.assertEqual(self.post('/api/admin/confirm-payment',{},cookie)[0],403)
  self.assertEqual(self.get('/api/downloads?order=TABO-0000000000000001',cookie['Cookie'])[0],403)
 def test_insight_cookie_no_private_values_and_same_origin_enforced(self):
  with patch.object(dashboard,'insight_start',return_value=('a'*64,1800)):
   status,data,headers=self.post('/api/store-insights/session',{'path':'/'});self.assertEqual(status,200)
   self.assertEqual(data,{'ok':True});self.assertIn(dashboard.INSIGHT_COOKIE,headers['Set-Cookie']);self.assertIn('HttpOnly',headers['Set-Cookie'])
  self.assertEqual(self.post('/api/store-insights/session',{}, {'Origin':'https://evil.invalid'})[0],403)
  with patch.dict(os.environ,{'TABO_DASHBOARD_ENABLED':'0'}):self.assertEqual(self.post('/api/store-insights/event',{'event':'Purchase'})[0],503)
 def test_only_public_pages_have_one_first_party_bootstrap(self):
  for path in ('/','/books/book20','/checkout','/contact','/offers'):
   code,body,_=self.get(path);self.assertEqual(code,200);self.assertEqual(body.count(b'/store-insights.js?v='),1)
  for path in ('/admin','/pending.html','/success.html','/order-help'):
   self.assertNotIn(b'/store-insights.js',self.get(path)[1])

 def test_promoter_management_is_owner_only_and_restricts_destinations(self):
  route = '/api/admin-dashboard/promoters'
  code = '1234567890abcdef'
  name = 'تيك توك - محكمة الحب و الجنس'
  create = {'action': 'create', 'name': '  ' + name + '  ', 'destination': '/books/book22'}
  self.assertEqual(self.get(route)[0], 403)
  self.assertEqual(self.post(route, create)[0], 403)
  with patch.object(dashboard, 'authenticated', return_value=True), patch.object(app.store, 'request', return_value=code) as request:
   self.assertEqual(self.post(route, {**create, 'destination': '//example.invalid'})[0], 400)
   self.assertEqual(self.post(route, {**create, 'destination': '/books/book9999'})[0], 400)
   self.assertEqual(self.post(route, create, {'Origin': 'https://example.invalid'})[0], 403)
   request.assert_not_called()
   status, reply, _ = self.post(route, create)
   self.assertEqual(status, 200)
   self.assertEqual(reply['url'], 'https://tabobooks.com/r/' + code)
   request.assert_called_once()
   path, method, params = request.call_args.args
   self.assertEqual((path, method), ('/rest/v1/rpc/owner_promoter_create', 'POST'))
   self.assertEqual(params['p_name'], name)
   self.assertEqual(params['p_destination'], '/books/book22')
   request.return_value = [{'code': code, 'name': name, 'opens': 5, 'sales': []}]
   status, body, _ = self.get(route)
   self.assertEqual(status, 200)
   result = json.loads(body)
   self.assertEqual(result['rows'][0]['url'], reply['url'])
   self.assertIn('/books/book22', result['destinations'])

 def test_promoter_delete_is_owner_only_and_safe_to_retry(self):
  route = '/api/admin-dashboard/promoters'
  code = '1234567890abcdef'
  delete = {'action': 'delete', 'code': code}
  self.assertEqual(self.post(route, delete)[0], 403)
  with patch.object(dashboard, 'authenticated', return_value=True), patch.object(app.store, 'request', return_value=[{'code': code}]) as request:
   self.assertEqual(self.post(route, {**delete, 'code': 'invalid'})[0], 400)
   self.assertEqual(self.post(route, delete, {'Origin': 'https://example.invalid'})[0], 403)
   request.assert_not_called()
   self.assertEqual(self.post(route, delete)[:2], (200, {'ok': True}))
   path, method, changes = request.call_args.args
   self.assertEqual((path, method), ('/rest/v1/owner_promoter_links', 'PATCH'))
   self.assertFalse(changes['active'])
   self.assertTrue(changes['deleted_at'].endswith('+00:00'))
   self.assertEqual(request.call_args.kwargs['params'], {'code': 'eq.' + code, 'deleted_at': 'is.null', 'select': 'code'})
   request.return_value = []
   self.assertEqual(self.post(route, delete)[:2], (200, {'ok': True}))
   self.assertEqual(self.post(route, {'action': 'setActive', 'code': code, 'active': True})[0], 404)
   self.assertEqual(request.call_args.kwargs['params']['deleted_at'], 'is.null')

 def test_promoter_redirect_cookie_cannot_be_forged_and_expires(self):
  class NoRedirect(HTTPRedirectHandler):
   def redirect_request(self, *args):
    return None

  self.opener = build_opener(ProxyHandler({}), NoRedirect())
  code = '1234567890abcdef'
  with patch.object(dashboard, 'rpc', return_value={'destination': '/books/book21', 'active': True}) as rpc:
   status, _, headers = self.get('/r/' + code + '?next=https://example.invalid')
   self.assertEqual((status, headers['Location'], headers['Cache-Control']), (302, '/books/book21', 'no-store'))
   cookie = SimpleCookie(headers['Set-Cookie'])[dashboard.PROMOTER_COOKIE]
   self.assertTrue(cookie['secure'] and cookie['httponly'])
   self.assertEqual(cookie['max-age'], str(7 * 24 * 3600))
   self.assertEqual(dashboard.promoter_code(cookie.value), code)
   self.assertIsNone(dashboard.promoter_code('0' * 16 + cookie.value[16:]))
   with patch.object(dashboard.time, 'time', return_value=int(cookie.value.split('.')[1])):
    self.assertIsNone(dashboard.promoter_code(cookie.value))
   rpc.return_value = {'destination': '/books/book21', 'active': False}
   status, _, headers = self.get('/r/' + code)
   self.assertEqual((status, headers['Location']), (302, '/books/book21'))
   self.assertIsNone(headers['Set-Cookie'])
   rpc.return_value = {'destination': '//example.invalid', 'active': True}
   self.assertEqual(self.get('/r/' + code)[2]['Location'], '/')
