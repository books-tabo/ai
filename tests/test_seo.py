"""Initial HTML, canonical identity, draft isolation and private-asset regressions."""
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
from xml.etree import ElementTree

import app
import catalog
import seo
from support import HTTPCase
from tools.audit_seo import Page


class PublicHTMLTests(HTTPCase):
    def get(self, path, host='tabobooks.com'):
        request = Request(self.base + path, headers={'Host':host,'User-Agent':'SyntheticBrowser/1'})
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        return response.status, response.headers, response.read().decode('utf-8', errors='replace')

    def test_all_books_have_content_in_initial_html_without_private_or_stats_reads(self):
        with patch.object(app.store,'original') as originals, patch.object(app.store,'book_stats') as stats:
            titles, descriptions = set(), set()
            for key in catalog.BOOKS:
                status, headers, source = self.get('/books/' + key + '?utm_source=facebook&fbclid=synthetic')
                self.assertEqual(status, 200)
                page = Page(source)
                self.assertEqual(page.h1, seo.BOOKS[key]['name'])
                self.assertIn('PDF', page.title)
                self.assertTrue(page.meta['description'])
                self.assertEqual([x['href'] for x in page.links if x.get('rel')=='canonical'], [seo.ORIGIN+'/books/'+key])
                self.assertNotIn('noindex',headers.get('X-Robots-Tag',''))
                self.assertIn(seo.BOOKS[key]['about'], source)
                self.assertIn('/authors/tabo-noor',page.anchors)
                self.assertIn('/categories/', source)
                self.assertIn('لمن يناسب الكتاب؟',source)
                self.assertIn('أسئلة عن الكتاب',source)
                self.assertIn('nonce=',source)
                self.assertNotIn('SSR_BOOK',source)
                self.assertIn('/seo.js?v='+seo.ASSET_VERSIONS['seo.js'],source)
                self.assertIn('/seo.css?v='+seo.ASSET_VERSIONS['seo.css'],source)
                titles.add(page.title);descriptions.add(page.meta['description'])
            self.assertEqual(len(titles),len(catalog.BOOKS));self.assertEqual(len(descriptions),len(catalog.BOOKS))
            originals.assert_not_called();stats.assert_not_called()

    def test_home_and_every_book_are_linked_without_javascript(self):
        status, _, source = self.get('/')
        self.assertEqual(status,200)
        page = Page(source)
        self.assertTrue(all('/books/'+key in page.anchors for key in catalog.BOOKS))
        for key, category in seo.CATEGORIES.items():
            self.assertIn('/categories/'+key,page.anchors)
            status, _, body = self.get('/categories/'+key)
            self.assertEqual(status,200)
            parsed = Page(body)
            self.assertTrue(all('/books/'+item in parsed.anchors for item in category['books']))
            self.assertTrue(parsed.h1)
        self.assertIn('google-site-verification',source)
        self.assertIn('facebook-domain-verification',source)

    def test_canonical_alias_and_invalid_ids_never_fall_back_to_another_book(self):
        status, _, source = self.get('/book.html?id=book19&utm_campaign=existing&fbclid=test')
        self.assertEqual(status,200)
        self.assertEqual(Page(source).h1,seo.BOOKS['book19']['name'])
        self.assertIn('href="https://tabobooks.com/books/book19"',source)
        for path in ['/books/book0','/books/book999','/books/book01','/books/book20/other','/book.html?id=book999','/book.html','/categories/novels','/categories/unknown','/authors/unknown']:
            self.assertEqual(self.get(path)[0],404,path)

    def test_structured_data_is_real_and_does_not_publish_variable_prices_or_ratings(self):
        for key in catalog.BOOKS:
            source = self.get('/books/'+key)[2]
            nodes = Page(source).graphs[0]['@graph']
            types = [n['@type'] for n in nodes]
            self.assertIn('Book',types);self.assertIn('BreadcrumbList',types)
            self.assertIn('Organization',types);self.assertIn('WebSite',types);self.assertIn('Person',types)
            book = next(n for n in nodes if n['@type']=='Book')
            self.assertEqual(book['inLanguage'],'ar')
            self.assertEqual(book['bookFormat'],'https://schema.org/EBook')
            self.assertFalse(book['isAccessibleForFree'])
            self.assertEqual(book['name'],Page(source).h1)
            self.assertNotIn('Offer',types);self.assertNotIn('Product',types)
            self.assertNotIn('aggregateRating',json.dumps(nodes))
            self.assertNotIn('downloadUrl',json.dumps(nodes))
            self.assertNotIn('isbn',book)

    def test_sitemap_robots_and_all_public_pages_are_consistent(self):
        status, _, source = self.get('/sitemap.xml')
        self.assertEqual(status,200)
        urls = [e.text for e in ElementTree.fromstring(source).iter() if e.tag.endswith('}loc')]
        self.assertEqual(len(urls),len(set(urls)))
        self.assertEqual(set(urls),{seo.ORIGIN+path for path in seo.public_paths()})
        self.assertNotIn('lastmod',source)
        for url in urls:
            self.assertNotRegex(url,r'pending|success|download|previews|api/|\?')
            status, headers, body = self.get(url.removeprefix(seo.ORIGIN))
            self.assertEqual(status,200,url)
            self.assertNotIn('noindex',headers.get('X-Robots-Tag',''))
            self.assertTrue(Page(body).meta.get('description'))
        self.assertEqual(self.get('/robots.txt')[0],200)
        self.assertIn('Sitemap: '+seo.ORIGIN+'/sitemap.xml',self.get('/robots.txt')[2])
        self.assertNotIn('Disallow:',self.get('/robots.txt')[2])

    def test_drafts_stay_off_production_and_out_of_the_sitemap(self):
        self.assertEqual(len(seo.ARTICLES),3)
        for slug in seo.ARTICLES:
            self.assertEqual(self.get('/articles/'+slug)[0],404)
            self.assertNotIn('/articles/'+slug,seo.public_paths())
        self.assertEqual(self.get('/articles')[0],404)
        with patch.dict(os.environ,{'TABO_SITE_ENV':'review'}):
            status, headers, body = self.get('/articles/learning-with-evidence')
            self.assertEqual(status,200)
            self.assertIn('noindex',headers['X-Robots-Tag'])
            self.assertIn('مسودة للمراجعة',body)
            self.assertNotIn('/articles/',self.get('/sitemap.xml')[2])

    def test_review_is_noindex_and_cannot_use_production_payments_or_tracking(self):
        with patch.dict(os.environ,{'TABO_SITE_ENV':'review'}):
            for path in ['/', '/books/book20', '/authors', '/categories/business', '/sitemap.xml', '/api/config']:
                status, headers, body = self.get(path)
                self.assertEqual(status,200)
                self.assertIn('noindex',headers['X-Robots-Tag'])
                self.assertNotIn('data-pixel-id',body)
            with patch.object(app.store,'insert') as insert, patch.object(app,'kashier_checkout') as checkout:
                status, _, _ = self.post('/api/orders',{'items':['book20']})
                self.assertEqual(status,503)
                insert.assert_not_called();checkout.assert_not_called()
            self.assertNotIn('Sitemap:',self.get('/robots.txt')[2])
        self.assertIn('noindex',self.get('/books/book20',host='tabo-books-seo-review.onrender.com')[1]['X-Robots-Tag'])

    def test_private_and_preview_policy_is_not_exposed_as_public_content(self):
        with patch.object(app.store,'original') as original:
            for path in ['/book20.pdf','/originals/book20.pdf','/book21.pdf','/originals/book21.pdf','/book22.pdf','/originals/book22.pdf','/content/books.json','/covers/../app.py']:
                self.assertEqual(self.get(path)[0],404,path)
            for book_id in ('book20','book21','book22'):
                self.assertNotIn('href="/previews/'+book_id+'"',self.get('/books/'+book_id)[2])
            self.assertIn('href="/previews/book19"',self.get('/books/book19')[2])
            original.assert_not_called()

    def test_safe_json_blocks_script_closing_and_metadata_escapes_attributes(self):
        payload = '</script><img src=x onerror="alert(1)">'
        encoded = seo.safe_json({'x':payload})
        self.assertNotIn('<',encoded)
        self.assertEqual(json.loads(encoded)['x'],payload)
        self.assertNotIn('" onerror',seo.escape(payload))


class CatalogMetadataTests(unittest.TestCase):
    def test_public_catalog_and_files_have_one_consistent_identity(self):
        self.assertEqual(set(seo.BOOKS),set(catalog.BOOKS))
        for key, book in seo.BOOKS.items():
            self.assertEqual(book['name'],catalog.BOOKS[key][0])
            self.assertTrue((Path(app.ROOT)/book['image'].lstrip('/')).is_file())
            self.assertGreater(book['imageWidth'],0);self.assertGreater(book['imageHeight'],0)
            self.assertTrue(all(other in catalog.BOOKS and other!=key for other in book['related']))
            self.assertTrue(seo.categories_for(key))
            self.assertGreater(book['pageCount'],0)
            source=seo.render((Path(app.ROOT)/'book.html').read_text(),'/books/'+key,'tabobooks.com','book.html')
            self.assertIn('عدد صفحات ملف PDF',source)
            self.assertIn('\"numberOfPages\":'+str(book['pageCount']),source)
        self.assertEqual(seo.BOOKS['book19']['pageCount'],309)
        self.assertEqual(len(seo.BOOKS['book19']['chapters']),20)
        self.assertEqual(seo.BOOKS['book20']['pageCount'],245)
        self.assertEqual(len(seo.BOOKS['book20']['chapters']),15)
        self.assertEqual(seo.BOOKS['book21']['pageCount'],471)
        self.assertEqual(len(seo.BOOKS['book21']['chapters']),15)
        self.assertEqual(seo.CATEGORIES['marriage-family']['books'],['book20','book21','book22'])
        for bid in ('book20','book21','book22'):
            self.assertEqual(seo.categories_for(bid)[0][0],'marriage-family')
