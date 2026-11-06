"""Synthetic Google Ads tests: no real order, customer or Google event."""
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
import google_ads_tracking as google
import meta_tracking
from support import ENV, HTTPCase, order


class GooglePayloadTests(unittest.TestCase):
    def test_actual_charge_currency_value_and_stable_private_transaction_id(self):
        current = google.PURCHASE_SINCE + timedelta(minutes=10)
        value = order()
        value.update(status='PAID', paymentMode='live', transactionId='synthetic-payment',
                     paidAt=current.isoformat(), total=829.27, currency='EGP')
        first = google.purchase(value, current)
        self.assertEqual(first['value'], 829.27)
        self.assertEqual(first['currency'], 'EGP')
        self.assertRegex(first['transaction_id'], r'^[a-f0-9]{64}$')
        self.assertEqual(set(first), {'transaction_id', 'value', 'currency'})
        for private in (value['id'], value['transactionId'], value['buyer']['name'], value['buyer']['phone']):
            self.assertNotIn(private, json.dumps(first, ensure_ascii=False))
        with patch.dict(os.environ, {'TABO_META_PIXEL_ID': '', 'TABO_SESSION_BINDING_KEY': 'rotated'}):
            self.assertEqual(google.purchase(value, current), first)
        # A gateway-field correction must not count the same order twice.
        self.assertEqual(google.purchase({**value, 'transactionId': 'corrected'}, current), first)
        self.assertNotEqual(google.purchase({**value, 'id': 'TABO-0000000000000002'}, current)['transaction_id'], first['transaction_id'])
        self.assertEqual(google.purchase({**value, 'total': 23, 'currency': 'USD'}, current)['value'], 23)

    def test_unpaid_test_invalid_old_and_preinstallation_orders_are_excluded(self):
        current = google.PURCHASE_SINCE + timedelta(hours=1)
        value = order()
        value.update(status='PAID', paymentMode='live', transactionId='synthetic', paidAt=current.isoformat())
        variants = [{'status': status} for status in ('PENDING', 'FAILED', 'CANCELLED', 'REFUNDED')]
        variants += [{'paymentMode': 'test'}, {'transactionId': None}, {'paidAt': None},
                     {'paidAt': (google.PURCHASE_SINCE-timedelta(seconds=1)).isoformat()},
                     {'paidAt': (current+timedelta(seconds=1)).isoformat()},
                     {'paidAt': current.replace(tzinfo=None).isoformat()},
                     {'total': 'NaN'}, {'total': 'Infinity'}, {'total': '1e999'}, {'total': 0},
                     {'total': -1}, {'currency': 'XXX'}, {'id': 'not-an-order'},
                     {'items': []}, {'items': [{'id': 'book999'}]}]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assertIsNone(google.purchase({**value, **changes}, current))
        self.assertIsNone(google.purchase(value, current+timedelta(hours=25)))
        with patch.dict(os.environ, {'TABO_GOOGLE_ADS_ID': ''}):
            self.assertIsNone(google.purchase(value, current))
            self.assertIsNotNone(meta_tracking.purchase(value, current))


class GoogleHTTPTests(HTTPCase):
    def get(self, path, headers=None):
        request = Request(self.base+path, headers={'User-Agent': 'SyntheticBrowser/1', **(headers or {})})
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def test_nonce_scoped_csp_and_bootstrap_on_public_pages(self):
        for path in ('/', '/books/book20', '/offers/self-growth', '/categories/business', '/authors/tabo-noor', '/privacy', '/success.html'):
            with self.subTest(path=path):
                code, body, headers = self.get(path)
                self.assertEqual(code, 200)
                self.assertEqual(body.count(b'src="/google-ads-tracking.js?'), 1)
                self.assertIn(b'data-google-tag-id="AW-18456253761"', body)
                self.assertIn(b'data-google-conversion-label="Z30jCMCguqYdEMGq0OBE"', body)
                self.assertRegex(body.decode(), r'<script nonce="[^"]+" src="/google-ads-tracking.js\?v=[a-f0-9]{12}"')
                policy = headers['Content-Security-Policy']
                self.assertIn('https://www.googleadservices.com', policy)
                self.assertIn('https://www.google.com.sa', policy)
                self.assertIn('frame-src https://www.googletagmanager.com;', policy)
                script_policy = next(x for x in policy.split(';') if x.strip().startswith('script-src '))
                self.assertNotIn('unsafe-inline', script_policy)
                self.assertNotIn('unsafe-eval', script_policy)
        code, body, headers = self.get('/google-ads-tracking.js')
        self.assertEqual(code, 200)
        self.assertIn(b'googletagmanager.com/gtag/js', body)
        self.assertIn('javascript', headers['Content-Type'])
        _, body, headers = self.get('/pending.html?key=synthetic')
        self.assertNotIn(b'/google-ads-tracking.js', body)
        self.assertNotIn('google', headers['Content-Security-Policy'])
        self.assertIn('noindex', headers['X-Robots-Tag'])

    def test_review_and_disabled_google_never_load_live_google_tag(self):
        for env in ({'TABO_SITE_ENV': 'review'}, {'TABO_GOOGLE_ADS_ID': ''}, {'TABO_GOOGLE_ADS_ID': 'AW-123<script>'}):
            with patch.dict(os.environ, env):
                for path in ('/', '/books/book20', '/success.html'):
                    _, body, headers = self.get(path)
                    self.assertNotIn(b'/google-ads-tracking.js', body)
                    self.assertNotIn('google', headers['Content-Security-Policy'])

    def test_conversion_requires_paid_browser_session_and_does_not_consume_downloads(self):
        current = datetime.now(timezone.utc)
        value = order()
        value.update(status='PAID', paymentMode='live', transactionId='synthetic-payment',
                     paidAt=current.isoformat(), total=400,
                     downloadSessionHash=app.hash_secret('synthetic-session'),
                     downloadSessionExpiresAt=(current+timedelta(hours=1)).isoformat(),
                     downloadDeviceHash=hmac.new(ENV['TABO_SESSION_BINDING_KEY'].encode(), b'SyntheticBrowser/1', hashlib.sha256).hexdigest())
        self.install(value)
        path = '/api/downloads?order='+value['id']
        cookie = {'Cookie': app.download_cookie_name(value['id'])+'=synthetic-session'}
        self.assertEqual(self.get(path)[0], 403)
        code, body, headers = self.get(path, cookie)
        self.assertEqual(code, 200)
        data = json.loads(body)
        first = data['googleAdsPurchase']
        self.assertEqual(first['value'], 400)
        self.assertEqual(first['currency'], 'EGP')
        self.assertIsNotNone(data['metaPurchase'])
        self.assertEqual(json.loads(self.get(path, cookie)[1])['googleAdsPurchase'], first)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.db.usage, {})
        status_data = json.loads(self.get('/api/order-status?key='+value['statusKey'])[1])
        self.assertNotIn('googleAdsPurchase', status_data)
        self.assertEqual(self.get(path, {**cookie, 'User-Agent': 'AnotherBrowser/1'})[0], 403)
        self.db.orders[value['id']]['paymentMode'] = 'test'
        self.assertIsNone(json.loads(self.get(path, cookie)[1])['googleAdsPurchase'])
        self.db.orders[value['id']]['paymentMode'] = 'live'
        self.db.orders[value['id']]['status'] = 'PENDING'
        self.assertEqual(self.get(path, cookie)[0], 403)
