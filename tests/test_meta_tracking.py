"""Synthetic tracking regressions; never contact Meta or real buyer records."""
import hashlib
import hmac
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request
from unittest.mock import patch

import app
import meta_tracking
from support import ENV, HTTPCase, order


class MetaHTTPTests(HTTPCase):
    def get(self, path, headers=None):
        request = Request(self.base + path, headers={'User-Agent': 'SyntheticBrowser/1', **(headers or {})})
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read(), response.headers

    def paid(self):
        current = datetime.now(timezone.utc)
        value = order()
        value.update(status='PAID', paymentMode='live', transactionId='SYNTHETIC-TRANSACTION',
                     paidAt=(current-timedelta(minutes=2)).isoformat(),
                     downloadSessionHash=app.hash_secret('synthetic-session'),
                     downloadSessionExpiresAt=(current+timedelta(hours=1)).isoformat(),
                     downloadDeviceHash=hmac.new(ENV['TABO_SESSION_BINDING_KEY'].encode(),
                                                b'SyntheticBrowser/1', hashlib.sha256).hexdigest())
        return value

    def test_public_verification_nonce_and_scoped_csp(self):
        for path in ('/', '/books/book1', '/success.html'):
            code, body, headers = self.get(path)
            self.assertEqual(code, 200)
            self.assertIn(b'data-pixel-id="1371060818078535"', body)
            self.assertRegex(body.decode(), r'<script nonce="[^"]+" src="/meta-tracking.js\?v=[a-f0-9]{12}"')
            self.assertIn('https://connect.facebook.net', headers['Content-Security-Policy'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
        _, body, _ = self.get('/')
        self.assertIn(b'<meta name="facebook-domain-verification" content="fzf9a2ww233qpjhtwbo4srsp7bpyts" />', body)
        for path in ('/pending.html', '/privacy'):
            _, body, headers = self.get(path)
            self.assertNotIn(b'/meta-tracking.js', body)
            self.assertNotIn('facebook', headers['Content-Security-Policy'])
        code, body, headers = self.get('/meta-tracking.js')
        self.assertEqual(code, 200)
        self.assertIn(b'trackSingle', body)
        self.assertIn('javascript', headers['Content-Type'])
        with patch.dict(os.environ, {'TABO_META_PIXEL_ID': ''}):
            _, body, headers = self.get('/')
            self.assertNotIn(b'/meta-tracking.js', body)
            self.assertNotIn('facebook', headers['Content-Security-Policy'])

    def test_paid_authenticated_conversion_is_stable_and_contains_no_buyer_fields(self):
        value = self.paid(); self.install(value)
        path = '/api/downloads?order=' + value['id']
        self.assertEqual(self.get(path)[0], 403)
        cookie = {'Cookie': app.download_cookie_name(value['id']) + '=synthetic-session'}
        code, body, headers = self.get(path, cookie)
        self.assertEqual(code, 200)
        conversion = json.loads(body)['metaPurchase']
        self.assertEqual(conversion['value'], 199)
        self.assertEqual(conversion['currency'], 'EGP')
        self.assertEqual(conversion['content_ids'], ['book1'])
        self.assertRegex(conversion['eventID'], r'^[0-9a-f]{64}$')
        for private in (value['id'], value['transactionId'], value['buyer']['name'], value['buyer']['phone'], 'synthetic-session'):
            self.assertNotIn(private, json.dumps(conversion, ensure_ascii=False))
        self.assertEqual(json.loads(self.get(path, cookie)[1])['metaPurchase'], conversion)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.db.usage, {})
        self.assertEqual(self.get(path, {**cookie, 'User-Agent': 'Other/1'})[0], 403)
        self.db.orders[value['id']]['status'] = 'REFUNDED'
        self.assertEqual(self.get(path, cookie)[0], 403)

    def test_test_or_old_purchase_never_becomes_a_live_conversion(self):
        value = self.paid(); self.install(value)
        path = '/api/downloads?order=' + value['id']
        headers = {'Cookie': app.download_cookie_name(value['id']) + '=synthetic-session'}
        for changes in ({'paymentMode': 'test'}, {'paidAt': '2026-10-18T12:00:00Z'},
                        {'paidAt': None}, {'paidAt': (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()}):
            self.db.orders[value['id']] = {**value, **changes}
            code, body, _ = self.get(path, headers)
            self.assertEqual(code, 200)
            self.assertIsNone(json.loads(body)['metaPurchase'])
        self.db.orders[value['id']] = {**value, 'status': 'PENDING'}
        self.assertEqual(self.get(path, headers)[0], 403)


class MetaPayloadTests(unittest.TestCase):
    def test_currency_actual_bundle_and_stability_without_service_keys(self):
        current = meta_tracking.PURCHASE_SINCE + timedelta(minutes=10)
        value = order(); value.update(status='PAID', paymentMode='live', transactionId='synthetic-tx',
                                     paidAt=current.isoformat(), total=29.99, currency='USD',
                                     items=[{'id': 'book1'}, {'id': 'book18'}])
        first = meta_tracking.purchase(value, current)
        self.assertEqual(first['value'], 29.99)
        self.assertEqual(first['num_items'], 2)
        with patch.dict(os.environ, {'SUPABASE_SECRET_KEY': 'rotated', 'TABO_SESSION_BINDING_KEY': 'rotated'}):
            self.assertEqual(meta_tracking.purchase(value, current), first)
        for changes in ({'status': 'REFUNDED'}, {'total': 'NaN'}, {'currency': 'XXX'},
                        {'items': [{'id': 'book999'}]}, {'transactionId': None}):
            self.assertIsNone(meta_tracking.purchase({**value, **changes}, current))


    def test_new_catalog_books_and_fixed_totals_remain_valid_tracking_payloads(self):
        current=meta_tracking.PURCHASE_SINCE+timedelta(minutes=10)
        value=order();value.update(status='PAID',paymentMode='live',transactionId='synthetic-new-book',paidAt=current.isoformat(),currency='EGP',total=500)
        for ids,total in [(['book20'],500),(['book19','book20'],699),(['book1','book2','book20'],799)]:
            result=meta_tracking.purchase({**value,'items':[{'id':book_id} for book_id in ids],'total':total},current)
            self.assertEqual(result['content_ids'],ids)
            self.assertEqual(result['value'],total)
