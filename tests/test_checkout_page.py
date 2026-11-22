"""Checkout is a private standalone page; public book identity stays intact."""
import json
import os
import re
from unittest.mock import patch
from urllib.request import Request
import app
import catalog
from support import HTTPCase


class CheckoutPageTests(HTTPCase):
    def get(self, path):
        with self.opener.open(Request(self.base+path,headers={'Host':'tabobooks.com'}),timeout=10) as response:
            return response.status,response.headers,response.read().decode()

    def test_checkout_has_initial_content_and_payment_order_without_private_reads(self):
        with patch.object(app.store,'original') as original,patch.object(app.store,'find') as orders:
            status,headers,source=self.get('/checkout?utm_source=facebook&fbclid=synthetic')
        self.assertEqual(status,200)
        self.assertIn('noindex',headers['X-Robots-Tag'])
        self.assertIn('no-store',headers['Cache-Control'])
        self.assertIn('سلة الكتب وإتمام الدفع',source)
        self.assertNotIn('cartModal',source)
        self.assertNotIn('متابعة التسوق',source)
        self.assertLess(source.index('id="checkoutBtn"'),source.index('id="paypalCheckoutBtn"'))
        self.assertIn('class="payment-button card-payment"',source)
        books=json.loads(re.search(r'id="checkout-catalog">(.*?)</script>',source).group(1))
        self.assertEqual({book['id'] for book in books},set(catalog.BOOKS))
        self.assertNotIn('.pdf',json.dumps(books))
        self.assertRegex(source,r'/checkout-core\.js\?v=[0-9a-f]{12}')
        self.assertRegex(source,r'/checkout-page\.js\?v=[0-9a-f]{12}')
        original.assert_not_called();orders.assert_not_called()

    def test_shared_assets_and_existing_book_links_keep_their_identity(self):
        for path in ('/checkout-core.js','/checkout-page.js','/offer-pricing.js'):
            self.assertEqual(self.get(path)[0],200)
        _,_,home=self.get('/')
        self.assertNotIn('id="cartModal"',home)
        self.assertIn('/checkout-core.js?v=',home)
        _,_,book=self.get('/books/book20?utm_source=facebook&fbclid=synthetic')
        self.assertIn('href="https://tabobooks.com/books/book20"',book)
        self.assertIn('id="cartLink" href="/checkout"',book)
        self.assertNotIn('/checkout',self.get('/sitemap.xml')[2])

    def test_checkout_keeps_tracking_but_review_never_injects_advertising(self):
        source=self.get('/checkout')[2]
        self.assertIn('data-pixel-id=',source)
        self.assertIn('data-google-tag-id=',source)
        with patch.dict(os.environ,{'TABO_SITE_ENV':'review'}):
            _,headers,source=self.get('/checkout')
            self.assertIn('noindex',headers['X-Robots-Tag'])
            self.assertNotIn('data-pixel-id=',source)
            self.assertNotIn('data-google-tag-id=',source)
