"""Authenticated HTTP fixtures; no real customer or external Snap requests."""
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request
from unittest.mock import patch

import app
import snap_tracking
from support import ENV, HTTPCase, order


class SnapHTTPTests(HTTPCase):
    def get(self, path, headers=None):
        request = Request(self.base + path, headers={'User-Agent': 'SyntheticBrowser/1', **(headers or {})})
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def test_scoped_bootstrap_nonce_and_csp(self):
        for path in ('/', '/books/book20', '/offers/self-growth', '/success.html'):
            with self.subTest(path=path):
                code, body, headers = self.get(path)
                self.assertEqual(code, 200)
                self.assertEqual(body.count(b'src="/snap-tracking.js?'), 1)
                self.assertIn(snap_tracking.PIXEL_ID.encode(), body)
                self.assertRegex(body.decode(), r'<script nonce="[^"]+" src="/snap-tracking.js\?v=[a-f0-9]{12}"')
                policy = headers['Content-Security-Policy']
                self.assertIn('https://sc-static.net', policy)
                self.assertIn('https://tr.snapchat.com', policy)
                scripts = next(x for x in policy.split(';') if x.strip().startswith('script-src '))
                self.assertNotIn('unsafe-inline', scripts)
                self.assertNotIn('unsafe-eval', scripts)
        code, body, headers = self.get('/snap-tracking.js')
        self.assertEqual(code, 200)
        self.assertIn(b'sc-static.net/scevent.min.js', body)
        self.assertIn('javascript', headers['Content-Type'])
        _, body, headers = self.get('/pending.html?key=synthetic')
        self.assertNotIn(b'/snap-tracking.js', body)
        self.assertNotIn('snapchat', headers['Content-Security-Policy'])

    def test_review_and_explicit_disable_load_no_snap(self):
        for env in ({'TABO_SITE_ENV': 'review'}, {'TABO_SNAP_PIXEL_ID': ''}, {'TABO_SNAP_PIXEL_ID': '<script>'}):
            with patch.dict(os.environ, env):
                for path in ('/', '/books/book20', '/success.html'):
                    _, body, headers = self.get(path)
                    self.assertNotIn(b'/snap-tracking.js', body)
                    self.assertNotIn('snapchat', headers['Content-Security-Policy'])

    def test_only_paid_device_session_receives_charge_without_consuming_download(self):
        current = datetime.now(timezone.utc)
        value = order()
        value.update(status='PAID', paymentMode='live', transactionId='synthetic-payment',
                     paidAt=current.isoformat(), total=829.27, currency='EGP',
                     downloadSessionHash=app.hash_secret('synthetic-session'),
                     downloadSessionExpiresAt=(current + timedelta(hours=1)).isoformat(),
                     downloadDeviceHash=hmac.new(ENV['TABO_SESSION_BINDING_KEY'].encode(),
                                                 b'SyntheticBrowser/1', hashlib.sha256).hexdigest())
        self.install(value)
        path = '/api/downloads?order=' + value['id']
        cookie = {'Cookie': app.download_cookie_name(value['id']) + '=synthetic-session'}
        self.assertEqual(self.get(path)[0], 403)
        with patch.object(snap_tracking, 'PURCHASE_SINCE', current - timedelta(minutes=1)):
            code, body, headers = self.get(path, cookie)
            self.assertEqual(code, 200)
            first = json.loads(body)['snapPurchase']
            self.assertEqual(first['value'], 829.27)
            self.assertEqual(first['currency'], 'EGP')
            self.assertRegex(first['transaction_id'], r'^[a-f0-9]{64}$')
            self.assertEqual(json.loads(self.get(path, cookie)[1])['snapPurchase'], first)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(self.db.usage, {})
            status_data = json.loads(self.get('/api/order-status?key=' + value['statusKey'])[1])
            self.assertNotIn('snapPurchase', status_data)
            self.assertEqual(self.get(path, {**cookie, 'User-Agent': 'AnotherBrowser/1'})[0], 403)
            self.db.orders[value['id']]['paymentMode'] = 'test'
            self.assertIsNone(json.loads(self.get(path, cookie)[1])['snapPurchase'])
            self.db.orders[value['id']]['paymentMode'] = 'live'
            self.db.orders[value['id']]['status'] = 'PENDING'
            self.assertEqual(self.get(path, cookie)[0], 403)
