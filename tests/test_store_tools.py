"""Audit tools stay owner-only; monitoring is opt-in and CSP-scoped."""
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

import app
import store_tools
from support import HTTPCase

DSN = 'https://' + 'a' * 32 + '@o123.ingest.us.sentry.io/456'


class StoreToolsTests(unittest.TestCase):
    def test_dsn_requires_explicit_production_opt_in_and_official_ingest(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(store_tools.sentry_config())
            os.environ.update(TABO_SENTRY_ENABLED='1', SENTRY_DSN=DSN)
            self.assertEqual(store_tools.sentry_config()['ingest'], 'https://o123.ingest.us.sentry.io')
            os.environ['TABO_SITE_ENV'] = 'review'
            self.assertIsNone(store_tools.sentry_config())
            os.environ['TABO_SITE_ENV'] = 'production'
            for bad in [DSN+'?token=private', DSN.replace('.io/', '.io.evil/'), DSN.replace('.io/', '.io:443/'),
                        DSN.replace('https:', 'http:'), DSN.replace('@o123', ':password@o123'), 'https://evil.com/456']:
                os.environ['SENTRY_DSN'] = bad
                self.assertIsNone(store_tools.sentry_config())

    def test_dashboard_links_are_fixed_public_targets_without_credentials(self):
        with patch.dict(os.environ, {'TABO_SENTRY_PROJECT_URL':'https://evil.com/?token=private'}, clear=True):
            result = store_tools.dashboard_tools()
            self.assertFalse(result['sentry']['enabled'])
            self.assertEqual(result['sentry']['url'], 'https://sentry.io/')
            self.assertNotIn('dsn', json.dumps(result).lower())
            for page in result['pagespeed']:
                for device in ('mobile', 'desktop'):
                    self.assertTrue(page[device].startswith('https://pagespeed.web.dev/analysis?url=https%3A%2F%2Ftabobooks.com%2F'))
                    self.assertIn('form_factor='+device, page[device])


class StoreToolsHTTPTests(HTTPCase):
    def get(self, path, host='tabobooks.com'):
        try:
            return self.opener.open(Request(self.base+path, headers={'Host':host}))
        except HTTPError as error:
            return error

    def test_monitoring_defaults_off_and_owner_scripts_are_versioned(self):
        with self.get('/') as response:
            self.assertNotIn('sentry', response.headers['Content-Security-Policy'])
            self.assertNotIn('error-monitoring.js', response.read().decode())
        with self.get('/admin') as response:
            self.assertRegex(response.read().decode(), r'admin-tools.js\?v=[a-f0-9]{12}')
        for script in ('admin-tools.js', 'error-monitoring.js'):
            with self.get('/'+script) as response:
                self.assertEqual(response.status, 200)

    def test_sentry_nonce_and_csp_only_on_production_storefront(self):
        with patch.dict(os.environ, {'TABO_SENTRY_ENABLED':'1','SENTRY_DSN':DSN}):
            for path in ('/', '/books/book22', '/checkout', '/success.html?order=private'):
                with self.get(path) as response:
                    source=response.read().decode()
                    self.assertIn('data-sentry-dsn="'+DSN+'"', source)
                    self.assertRegex(source, r'<script nonce="[^"]+" src="/error-monitoring.js\?v=')
                    csp=response.headers['Content-Security-Policy']
                    self.assertIn(' https://browser.sentry-cdn.com', csp)
                    self.assertIn(' https://o123.ingest.us.sentry.io;', csp)
                    self.assertNotIn('*.sentry.io', csp)
            for path, host in [('/admin','tabobooks.com'), ('/','preview.onrender.com')]:
                with self.get(path, host) as response:
                    self.assertNotIn('error-monitoring.js',response.read().decode())
                    self.assertNotIn('sentry',response.headers['Content-Security-Policy'])

    def test_tools_endpoint_requires_owner_session(self):
        with patch.object(app.owner_dashboard,'enabled',return_value=True), patch.object(app.owner_dashboard,'authenticated',return_value=None):
            with self.get('/api/admin-dashboard/tools') as response:
                self.assertEqual(response.status,403)
        with patch.object(app.owner_dashboard,'enabled',return_value=True), patch.object(app.owner_dashboard,'authenticated',return_value={'email':'owner@example.invalid'}):
            with self.get('/api/admin-dashboard/tools?url=https://evil.com') as response:
                self.assertEqual(response.status,200)
                result=json.load(response)
                self.assertNotIn('evil.com',json.dumps(result))
                self.assertFalse(result['sentry']['enabled'])
                self.assertIn('no-store',response.headers['Cache-Control'])
