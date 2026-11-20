import unittest
from unittest.mock import patch
from testing import paypal_sandbox as sandbox


ENV = {'TABO_ENV': 'paypal-sandbox', 'TABO_SITE_ENV': 'review', 'PAYPAL_MODE': 'test',
       'TABO_PUBLIC_URL': 'https://paypal-test.onrender.com'}


class SandboxBoundaryTests(unittest.TestCase):
    def test_only_an_explicit_nonproduction_environment_is_accepted(self):
        sandbox.validate_environment(ENV)
        for name, value in (('TABO_ENV', 'production'), ('TABO_SITE_ENV', 'production'),
                            ('PAYPAL_MODE', 'live'), ('TABO_PUBLIC_URL', 'https://tabobooks.com'),
                            ('TABO_PUBLIC_URL', 'https://tabo-books-preview.onrender.com'),
                            ('TABO_PUBLIC_URL', 'https://user:secret@example.com'),
                            ('TABO_PUBLIC_URL', 'https://test.example/path')):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                sandbox.validate_environment({**ENV, name: value})

    def test_production_storage_ad_and_gateway_credentials_are_refused(self):
        for name in ('PAYPAL_LIVE_CLIENT_SECRET', 'SUPABASE_URL', 'SUPABASE_SECRET_KEY',
                     'KASHIER_LIVE_SECRET_KEY', 'PAYMOB_TEST_SECRET_KEY', 'TABO_ADMIN_KEY',
                     'TABO_META_PIXEL_ID', 'TABO_GOOGLE_ADS_ID', 'TABO_SNAP_PIXEL_ID'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                sandbox.validate_environment({**ENV, name: 'synthetic-forbidden-value'})

    def test_database_connection_cannot_target_a_remote_or_non_test_database(self):
        sandbox.validate_dsn('host=127.0.0.1 dbname=tabo_test_paypal user=postgres')
        for dsn in ('host=prod.example dbname=tabo_test', 'host=127.0.0.1 dbname=postgres',
                    'host=127.0.0.1 hostaddr=192.0.2.10 dbname=tabo_test',
                    "host=localhost dbname=tabo_test options='-c search_path=public'"):
            with self.subTest(dsn=dsn), self.assertRaises(ValueError): sandbox.validate_dsn(dsn)

    def test_generated_original_is_synthetic_and_does_not_read_a_paid_book(self):
        from pypdf import PdfReader
        from io import BytesIO
        with patch.object(sandbox.Path, 'read_bytes', side_effect=AssertionError('No originals may be read')):
            pdf = sandbox.synthetic_pdf()
        reader = PdfReader(BytesIO(pdf))
        self.assertEqual(len(reader.pages), 1)
        self.assertIn('NOT A PAID BOOK', reader.pages[0].extract_text())


if __name__ == '__main__': unittest.main()
